"""Verify access tokens with Supabase Auth and authorize one explicit owner."""
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request as URLRequest, urlopen

from fastapi import HTTPException, Request
from .deployment import auth_required

OAUTH_NAVIGATION = {'/api/settings/gmail/authorize', '/api/settings/gmail/callback'}


def require_user(request: Request):
    if request.url.path == '/healthz' or request.url.path in OAUTH_NAVIGATION:
        # These OAuth navigations are protected by short-lived, one-use state,
        # PKCE and a first-party HttpOnly cookie. The initial connect requires auth.
        return None
    if not auth_required():
        return None
    header = request.headers.get('authorization', '')
    scheme, _, token = header.partition(' ')
    if scheme.lower() != 'bearer' or not token.strip() or len(token) > 16000:
        raise HTTPException(401, 'Inicia sesión para acceder.', headers={'WWW-Authenticate': 'Bearer'})
    base = os.environ.get('SUPABASE_URL', '').rstrip('/')
    key, owner = os.environ.get('SUPABASE_ANON_KEY'), os.environ.get('APP_OWNER_ID')
    if not base.startswith('https://') or not key or not owner:
        raise HTTPException(503, 'La autenticación del servidor no está configurada.')
    try:
        query = URLRequest(base + '/auth/v1/user', headers={'apikey': key, 'Authorization': 'Bearer ' + token})
        with urlopen(query, timeout=10) as response:
            user = json.loads(response.read(100001))
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise HTTPException(401, 'La sesión no es válida o ha caducado.') from None
        raise HTTPException(503, 'No se pudo verificar la sesión.') from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise HTTPException(503, 'No se pudo verificar la sesión.') from None
    if not isinstance(user, dict) or not user.get('id'):
        raise HTTPException(401, 'La sesión no es válida.')
    if user['id'] != owner or user.get('is_anonymous'):
        raise HTTPException(403, 'Esta cuenta no tiene acceso a JobHunt.')
    request.state.user_id = user['id']
    return user
