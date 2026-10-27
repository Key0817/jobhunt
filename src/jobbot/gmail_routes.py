"""OAuth settings endpoints for the local, single-user dashboard."""
import html
import json
import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from . import gmail_oauth as oauth


class ConnectRequest(BaseModel):
    folder: str = Field(default="JobAlerts", min_length=1, max_length=150)


def protect_local(request: Request, mutation=False):
    if request.url.hostname not in {"localhost", "127.0.0.1"} or not request.client or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(status_code=403, detail="Los ajustes de Gmail solo están disponibles desde el equipo local.")
    origin = request.headers.get("origin")
    if origin is not None and origin not in oauth.FRONTEND_ORIGINS:
        raise HTTPException(status_code=403, detail="Origen no permitido.")
    if mutation and (origin not in oauth.FRONTEND_ORIGINS or request.headers.get("content-type", "").split(";")[0] != "application/json"):
        raise HTTPException(status_code=403, detail="Inicia esta acción desde Ajustes de JobHunt.")


def callback_page(origin, success, message):
    nonce = secrets.token_urlsafe(18)
    payload = json.dumps({"type": "jobhunt-gmail-oauth", "success": success, "message": message}).replace("<", "\u003c")
    body = f"""<!doctype html><html lang="es"><meta charset="utf-8"><title>Conexión Gmail</title>
<body><h1>{'Gmail conectado' if success else 'No se completó la conexión'}</h1><p>{html.escape(message)}</p>
<p>Puedes volver a Ajustes de JobHunt.</p><script nonce="{nonce}">
history.replaceState(null, '', '/api/settings/gmail/result');
if (window.opener) {{window.opener.postMessage({payload}, {json.dumps(origin)}); window.close();}}
</script></body></html>"""
    response = HTMLResponse(body, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": f"default-src 'none'; script-src 'nonce-{nonce}'; frame-ancestors 'none'"})
    for name in ("gmail_oauth_state", "gmail_oauth_origin"):
        response.delete_cookie(name, path="/api/settings/gmail", httponly=True, samesite="lax")
    return response


def create_router(get_db):
    router = APIRouter(prefix="/api/settings/gmail", tags=["Gmail"])

    @router.get("")
    def status(request: Request, db=Depends(get_db)):
        protect_local(request)
        return JSONResponse(oauth.connection_status(db), headers={"Cache-Control": "no-store"})

    @router.post("/connect")
    def connect(request: Request, body: ConnectRequest, db=Depends(get_db)):
        protect_local(request, mutation=True)
        origin = request.headers["origin"]
        try:
            url, state = oauth.begin_connection(db, origin, body.folder)
        except oauth.GmailOAuthError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        launch = oauth.OAuthConfig.load().redirect_uri.removesuffix("/callback") + "/authorize?" + urlencode({"state": state})
        response = JSONResponse({"authorization_url": url, "launch_url": launch}, headers={"Cache-Control": "no-store"})
        for name, value in (("gmail_oauth_state", state), ("gmail_oauth_origin", origin)):
            response.set_cookie(name, value, max_age=600, path="/api/settings/gmail", httponly=True, samesite="lax")
        return response

    @router.get("/authorize")
    def authorize(request: Request, state: str = "", db=Depends(get_db)):
        request.scope["query_string"] = b""
        protect_local(request)
        try:
            url, origin = oauth.browser_authorization(db, state)
        except oauth.GmailOAuthError as problem:
            return callback_page("http://localhost:5173", False, str(problem))
        response = RedirectResponse(url, status_code=302, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
        for name, value in (("gmail_oauth_state", state), ("gmail_oauth_origin", origin)):
            response.set_cookie(name, value, max_age=600, path="/api/settings/gmail", httponly=True, samesite="lax")
        return response

    @router.get("/callback")
    def callback(request: Request, state: str = "", code: str = "", error: str = "", db=Depends(get_db)):
        # Parameters have been parsed; keep temporary authorization codes out of the access log.
        request.scope["query_string"] = b""
        protect_local(request)
        origin = request.cookies.get("gmail_oauth_origin", "http://localhost:5173")
        if origin not in oauth.FRONTEND_ORIGINS:
            origin = "http://localhost:5173"
        try:
            origin = oauth.finish_connection(db, state, request.cookies.get("gmail_oauth_state"), code, denied=bool(error))
            return callback_page(origin, True, "Gmail quedó conectado para importar alertas en modo de solo lectura.")
        except oauth.GmailOAuthError as problem:
            return callback_page(origin, False, str(problem))

    @router.post("/check")
    def check(request: Request, db=Depends(get_db)):
        protect_local(request, mutation=True)
        try:
            exists = oauth.check_label(db)
        except oauth.GmailOAuthError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        return {"label_exists": exists}

    @router.post("/disconnect")
    def disconnect(request: Request, db=Depends(get_db)):
        protect_local(request, mutation=True)
        revoked = oauth.disconnect(db)
        return {"disconnected": True, "revoked": revoked}

    return router
