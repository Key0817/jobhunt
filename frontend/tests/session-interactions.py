"""Browser regression check. Run from repo root after a build with fake test URLs.

VITE_API_URL=https://api.jobhunt.test
VITE_SUPABASE_URL=https://project.supabase.co
VITE_SUPABASE_ANON_KEY=test-public-key
All external requests are intercepted; no real accounts or APIs are used.
"""
import asyncio
import base64
import json
import threading
import time
from collections import Counter
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from playwright.async_api import async_playwright, expect


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass


async def main():
    assert any('https://api.jobhunt.test' in p.read_text(encoding='utf-8') for p in Path('frontend/dist/assets').glob('*.js')), 'Build with the fake test environment first.'
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Quiet, directory='frontend/dist'))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f'http://127.0.0.1:{server.server_port}'
    encode = lambda value: base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
    token = lambda n: encode({'alg': 'HS256'}) + '.' + encode({'sub': 'owner', 'exp': int(time.time()) + 3600, 'iat': int(time.time()) + n, 'aud': 'authenticated'}) + '.c2ln'
    tokens = [token(0), token(2)]
    user = dict(id='owner', aud='authenticated', role='authenticated', email='owner@example.com', app_metadata={'provider': 'email'}, user_metadata={}, created_at='2026-10-30T00:00:00Z')
    jobs = [dict(id=str(n), title=f'Vacante {n:03}', company='Empresa', status='encontrada', score=100-n,
                 url=f'https://example.com/{n}', source='getonboard', origins=[], remote=True,
                 location='CR', detected_at='2026-10-30T12:00:00Z') for n in range(1, 56)]
    cv = dict(id='cv1', job_id='1', job_title='Vacante 001', company='Empresa', variant='ai', profile_key='ai',
              status='pending_review', approved=False, model_name='test', profile_text='Perfil de prueba',
              cover_letter=None, created_at='2026-10-30T12:00:00Z')
    requests, logout_scopes = [], []
    state = {'gate': None, 'fail': False, 'refreshes': 0, 'reject_cvs_once': False}
    statuses = ['encontrada', 'aplicable', 'cv_listo', 'solicitada', 'entrevista', 'oferta', 'rechazada', 'descartada']

    async def router(route):
        request = route.request
        url = urlsplit(request.url)
        cors = {'Access-Control-Allow-Origin': origin, 'Access-Control-Allow-Credentials': 'true', 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': '*'}
        if url.netloc == urlsplit(origin).netloc:
            await route.continue_()
            return
        if request.method == 'OPTIONS':
            await route.fulfill(status=204, headers=cors)
            return
        if url.netloc == 'project.supabase.co':
            if url.path.endswith('/logout'):
                logout_scopes.append(parse_qs(url.query).get('scope', [''])[0])
                # Already-revoked sessions can return 403; the SDK must still clear locally.
                await route.fulfill(status=403, json={'code': 'session_not_found', 'message': 'Session not found'}, headers=cors)
                return
            if parse_qs(url.query).get('grant_type') == ['refresh_token']:
                state['refreshes'] += 1
                access = tokens[1]
            else:
                access = tokens[0]
            await route.fulfill(json=dict(access_token=access, refresh_token='refresh', token_type='bearer', expires_in=3600, expires_at=int(time.time())+3600, user=user), headers=cors)
            return
        if url.netloc != 'api.jobhunt.test':
            await route.abort()
            raise AssertionError('Unexpected external request: ' + url.netloc)
        assert request.headers.get('authorization') in ['Bearer ' + t for t in tokens]
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        requests.append((request.method, url.path, query))
        if url.path == '/api/auth/me':
            result = {'id': 'owner'}
        elif url.path == '/api/stats':
            result = dict(total_jobs=len(jobs), new_last_7_days=0, applied=0, interviews=0, offers=0,
                          by_status={s: sum(j['status'] == s for j in jobs) for s in statuses}, weekly_applications=[])
        elif url.path == '/api/jobs/page':
            rows = [j for j in jobs if not query.get('status') or query['status'] == j['status']]
            size = int(query['page_size'])
            pages = max(1, (len(rows)+size-1)//size)
            page = min(int(query['page']), pages)
            result = dict(items=rows[(page-1)*size:page*size], total=len(rows), page=page, page_size=size, total_pages=pages)
        elif request.method == 'PATCH' and url.path.startswith('/api/jobs/'):
            if state['gate']:
                await asyncio.wait_for(state['gate'].wait(), timeout=15)
            if state['fail']:
                await route.fulfill(status=500, json={'detail': 'Simulated failure'}, headers=cors)
                return
            row = next(j for j in jobs if j['id'] == url.path.split('/')[-2])
            row['status'] = request.post_data_json['status']
            result = {'id': row['id'], 'status': row['status']}
        elif url.path == '/api/cvs':
            if state['reject_cvs_once']:
                state['reject_cvs_once'] = False
                await route.fulfill(status=401, json={'detail': 'expired'}, headers=cors)
                return
            result = [cv]
        elif url.path == '/api/cvs/cv1':
            result = cv
        elif url.path == '/api/cvs/cv1/status':
            cv.update(status=request.post_data_json['status'], approved=request.post_data_json['status'] == 'approved')
            result = cv
        elif url.path.startswith('/api/jobs/') and url.path.endswith('/cv'):
            result = [cv]
        elif url.path.startswith('/api/jobs/'):
            result = next(j for j in jobs if j['id'] == url.path.rsplit('/', 1)[-1])
        else:
            raise AssertionError(url.path)
        await route.fulfill(json=result, headers=cors)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(viewport={'width': 1500, 'height': 1100})
            await context.route('**/*', router)
            page = await context.new_page()
            page.set_default_timeout(7000)
            page.on('pageerror', lambda error: print('Browser error:', error, flush=True))
            await page.goto(origin)
            print('Login page ready', flush=True)
            await page.get_by_label('Correo', exact=True).fill('owner@example.com')
            await page.get_by_label('Contraseña', exact=True).fill('fake-password')
            await page.get_by_role('button', name='Iniciar sesión', exact=True).click()
            found = page.get_by_role('region', name='Encontradas', exact=True)
            applicable = page.get_by_role('region', name='Aplicables', exact=True)
            await expect(found.get_by_role('button', name='Vacante 001', exact=True)).to_be_visible()
            await page.wait_for_timeout(150)
            before = len(requests)
            gate = state['gate'] = asyncio.Event()
            await found.get_by_label('Estado de Vacante 001').select_option('aplicable')
            # The response is held: the card must move before the network succeeds.
            await expect(applicable.get_by_role('button', name='Vacante 001', exact=True)).to_be_visible()
            await expect(found.get_by_role('button', name='Vacante 001', exact=True)).to_have_count(0)
            gate.set()
            await expect(applicable.get_by_label('Estado de Vacante 001')).to_be_enabled()
            await page.wait_for_timeout(250)
            fetched_statuses = [q['status'] for method, path, q in requests[before:] if path == '/api/jobs/page']
            assert sorted(fetched_statuses) == ['aplicable', 'encontrada'], fetched_statuses
            print('OK: optimistic move and only two affected columns fetched.')
            state['gate'], state['fail'] = None, True
            await found.get_by_label('Estado de Vacante 002').select_option('aplicable')
            await expect(found.get_by_label('Estado de Vacante 002')).to_be_enabled()
            await expect(found.get_by_role('button', name='Vacante 002', exact=True)).to_be_visible()
            state['fail'] = False
            print('OK: failed PATCH rolls the card back.')
            await page.get_by_role('button', name='Ofertas', exact=True).click()
            await expect(page.get_by_label('Estado de Vacante 003')).to_be_visible()
            before = len(requests)
            await page.get_by_label('Estado de Vacante 003').select_option('entrevista')
            await expect(page.get_by_label('Estado de Vacante 003')).to_have_value('entrevista')
            await expect(page.get_by_label('Estado de Vacante 003')).to_be_enabled()
            await page.wait_for_timeout(150)
            assert not any(path == '/api/jobs/page' for _, path, _ in requests[before:])
            await page.get_by_role('button', name='Página siguiente de ofertas').first.click()
            await expect(page.get_by_role('button', name='Vacante 055', exact=True)).to_be_visible()
            await page.get_by_label('Orden', exact=False).select_option('score')
            await expect(page.get_by_label('Estado de Vacante 003')).to_be_visible()
            await page.get_by_role('button', name='Página siguiente de ofertas').first.click()
            await expect(page.get_by_role('button', name='Vacante 055', exact=True)).to_be_visible()
            me_before = sum(path == '/api/auth/me' for _, path, _ in requests)
            # Exercise the SDK's real tab-visibility recovery, not a mocked auth callback.
            await page.evaluate("""() => {
              Object.defineProperty(document,'visibilityState',{configurable:true,get:()=> 'hidden'});
              document.dispatchEvent(new Event('visibilitychange'));
              Object.defineProperty(document,'visibilityState',{configurable:true,get:()=> 'visible'});
              document.dispatchEvent(new Event('visibilitychange'));
              window.dispatchEvent(new Event('focus'));
            }""")
            await page.wait_for_timeout(300)
            await expect(page.get_by_role('button', name='Vacante 055', exact=True)).to_be_visible()
            assert sum(path == '/api/auth/me' for _, path, _ in requests) == me_before
            await expect(page.get_by_label('Orden', exact=False)).to_have_value('score')
            print('OK: refocus retains view, page, filters and verified session.')
            await page.get_by_role('button', name='Vacante 055', exact=True).click()
            await expect(page.get_by_role('heading', name='Vacante 055', exact=True)).to_be_visible()
            await page.get_by_role('button', name='Volver', exact=True).click()
            await expect(page.get_by_role('button', name='Vacante 055', exact=True)).to_be_visible()
            await expect(page.get_by_label('Orden', exact=False)).to_have_value('score')
            print('OK: returning from a job detail keeps the selected offers page.')
            state['reject_cvs_once'] = True
            await page.get_by_role('button', name='Mi Currículum', exact=True).click()
            await expect(page.get_by_role('button', name='Ver y editar', exact=True)).to_be_visible()
            assert state['refreshes'] == 1
            print('OK: one 401 triggers one refresh and a successful retry.')
            await page.get_by_role('button', name='Ver y editar', exact=True).click()
            await expect(page.get_by_label('Estado del CV')).to_be_visible()
            await page.get_by_label('Estado del CV').select_option('approved')
            await expect(page.get_by_text('Estado guardado', exact=True)).to_be_visible()
            await page.get_by_role('button', name='Volver a mis CV', exact=True).click()
            await expect(page.get_by_text('Aprobado', exact=True)).to_be_visible()
            await expect(page.get_by_text('Cargando currículums…', exact=True)).to_have_count(0)
            print('OK: CV status and library cache stay synchronized.')
            await page.get_by_role('button', name='Cerrar sesión', exact=True).evaluate('(button) => { button.click(); button.click(); }')
            await expect(page.get_by_role('heading', name='Acceder a JobHunt')).to_be_visible()
            assert logout_scopes == ['local'], logout_scopes
            print('OK: a single local logout clears even an already-revoked session.')
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()


asyncio.run(main())
