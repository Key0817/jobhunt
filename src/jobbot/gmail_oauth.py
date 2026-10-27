"""Single-user local OAuth connection shared with unattended collectors."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
import secrets
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from cryptography.fernet import Fernet, InvalidToken
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest
import requests

from . import repo

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
FRONTEND_ORIGINS = {"http://localhost:5173", "http://127.0.0.1:5173"}
REDIRECT = "http://localhost:8000/api/settings/gmail/callback"


class GmailOAuthError(RuntimeError):
    pass


@dataclass(frozen=True)
class OAuthConfig:
    client_id: str
    client_secret: str = field(repr=False)
    redirect_uri: str

    @classmethod
    def load(cls):
        if missing_config():
            raise GmailOAuthError("Completa la configuración OAuth del backend: " + ", ".join(missing_config()))
        cipher()
        redirect = os.environ.get("GMAIL_OAUTH_REDIRECT_URI", REDIRECT)
        parts = urlsplit(redirect)
        if parts.hostname not in {"localhost", "127.0.0.1"} or parts.scheme not in {"http", "https"} or parts.path != "/api/settings/gmail/callback" or parts.query or parts.fragment or parts.username:
            raise GmailOAuthError("El callback OAuth debe ser una URL local /api/settings/gmail/callback.")
        return cls(os.environ["MAIL_OAUTH_CLIENT_ID"].strip(), os.environ["MAIL_OAUTH_CLIENT_SECRET"].strip(), redirect)


def missing_config():
    return [name for name in ("MAIL_OAUTH_CLIENT_ID", "MAIL_OAUTH_CLIENT_SECRET", "GMAIL_TOKEN_KEY") if not os.environ.get(name, "").strip()]


def cipher():
    try:
        return Fernet(os.environ.get("GMAIL_TOKEN_KEY", "").encode())
    except (ValueError, TypeError):
        raise GmailOAuthError("GMAIL_TOKEN_KEY no es una clave Fernet válida. Genera una y conserva la misma en backend y Actions.") from None


def encrypt(value):
    return cipher().encrypt(json.dumps(value).encode()).decode()


def decrypt(value):
    try:
        return json.loads(cipher().decrypt(value.encode()))
    except (InvalidToken, ValueError, TypeError, AttributeError):
        raise GmailOAuthError("No se puede descifrar la conexión Gmail. Revisa GMAIL_TOKEN_KEY o vuelve a conectar la cuenta.") from None


def get_connection(db):
    rows = db.query("select * from gmail_connections where id = ?", ("gmail",))
    return rows[0] if rows else None


def connection_status(db):
    row = get_connection(db)
    missing = missing_config()
    problem = None
    if not missing:
        try:
            OAuthConfig.load()
        except GmailOAuthError as error:
            problem = str(error)
    return {"configured": not missing and not problem, "missing": missing, "configuration_error": problem,
            "connected": bool(row and row["encrypted_credentials"] and row["enabled"]),
            "email": row["email"] if row and row["enabled"] else None,
            "folder": row["folder"] if row else "JobAlerts",
            "redirect_uri": os.environ.get("GMAIL_OAUTH_REDIRECT_URI", REDIRECT)}


def _flow(config, state, verifier):
    return Flow.from_client_config({"web": {"client_id": config.client_id, "client_secret": config.client_secret,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [config.redirect_uri]}}, scopes=[SCOPE], state=state,
        redirect_uri=config.redirect_uri, code_verifier=verifier, autogenerate_code_verifier=False)


def begin_connection(db, origin, folder):
    config = OAuthConfig.load()
    if origin not in FRONTEND_ORIGINS:
        raise GmailOAuthError("Origen de la aplicación no permitido.")
    if not folder.strip() or len(folder) > 150 or any(c in folder for c in "\r\n"):
        raise GmailOAuthError("Indica una etiqueta válida, por ejemplo JobAlerts.")
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    flow = _flow(config, state, verifier)
    url, _ = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="false")
    with db.transaction():
        db.execute("delete from gmail_oauth_states where expires_at < ?", (int(time.time()),))
        db.execute("insert into gmail_oauth_states (state_hash, encrypted_verifier, frontend_origin, folder, expires_at) values (?, ?, ?, ?, ?)",
                   (hashlib.sha256(state.encode()).hexdigest(), encrypt({"verifier": verifier}), origin, folder.strip(), int(time.time()) + 600))
    return url, state


def browser_authorization(db, state):
    """Set the browser cookie on a top-level backend navigation before Google."""
    if not state or len(state) > 128:
        raise GmailOAuthError("La solicitud de conexión no es válida. Iníciala desde Ajustes.")
    rows = db.query("select encrypted_verifier, frontend_origin from gmail_oauth_states where state_hash = ? and expires_at >= ?",
                    (hashlib.sha256(state.encode()).hexdigest(), int(time.time())))
    db.conn.commit()
    if not rows:
        raise GmailOAuthError("La solicitud de conexión caducó. Iníciala de nuevo desde Ajustes.")
    row = rows[0]
    verifier = decrypt(row["encrypted_verifier"])["verifier"]
    flow = _flow(OAuthConfig.load(), state, verifier)
    url, _ = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="false")
    return url, row["frontend_origin"]


def exchange_code(config, state, verifier, code):
    try:
        flow = _flow(config, state, verifier)
        flow.fetch_token(code=code, timeout=20)
        token = flow.oauth2session.token
        granted = token.get("scope", [])
        granted = granted.split() if isinstance(granted, str) else granted
        if SCOPE not in granted or not flow.credentials.refresh_token:
            raise GmailOAuthError("Google no concedió lectura de Gmail y acceso sin conexión. Vuelve a conectar y acepta ese permiso.")
        return flow.credentials.token, flow.credentials.refresh_token
    except GmailOAuthError:
        raise
    except Exception:
        raise GmailOAuthError("No se pudo completar la autorización con Google. Revisa el cliente OAuth y la URL de retorno.") from None


def gmail_get(token, path, query=""):
    request = Request("https://gmail.googleapis.com/gmail/v1/users/me/" + path + ("?" + query if query else ""),
                      headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
    try:
        with urlopen(request, timeout=15) as response:
            raw = response.read(4_000_001)
        if len(raw) > 4_000_000:
            raise GmailOAuthError("La respuesta de Gmail es demasiado grande.")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError
        return result
    except HTTPError as error:
        message = "Google rechazó el acceso. Revisa que Gmail API esté habilitada y vuelve a conectar en Ajustes." if error.code in (401, 403) else "La API de Gmail no pudo completar la consulta. Intenta más tarde."
        raise GmailOAuthError(message) from None
    except GmailOAuthError:
        raise
    except Exception:
        raise GmailOAuthError("No se pudo consultar Gmail. Revisa la conexión y vuelve a intentarlo.") from None


def finish_connection(db, state, cookie_state, code, denied=False):
    if not state or not cookie_state or not secrets.compare_digest(state, cookie_state):
        raise GmailOAuthError("La sesión de autorización no coincide. Inicia la conexión desde Ajustes de nuevo.")
    with db.transaction():
        rows = db.query("delete from gmail_oauth_states where state_hash = ? and expires_at >= ? returning encrypted_verifier, frontend_origin, folder",
                        (hashlib.sha256(state.encode()).hexdigest(), int(time.time())))
    if not rows:
        raise GmailOAuthError("La autorización caducó o ya fue utilizada. Vuelve a conectar desde Ajustes.")
    row = rows[0]
    if denied or not code:
        raise GmailOAuthError("No se concedió acceso a Gmail. Puedes intentarlo de nuevo desde Ajustes.")
    config = OAuthConfig.load()
    verifier = decrypt(row["encrypted_verifier"])["verifier"]
    access, refresh = exchange_code(config, state, verifier, code)
    profile = gmail_get(access, "profile")
    email = profile.get("emailAddress")
    if not isinstance(email, str) or "@" not in email:
        raise GmailOAuthError("Google no devolvió una cuenta Gmail válida.")
    with db.transaction():
        db.execute("insert into gmail_connections (id, email, folder, encrypted_credentials, enabled, connected_at) values (?, ?, ?, ?, ?, ?) "
                   "on conflict (id) do update set email = excluded.email, folder = excluded.folder, encrypted_credentials = excluded.encrypted_credentials, enabled = excluded.enabled, connected_at = excluded.connected_at",
                   ("gmail", email, row["folder"], encrypt({"refresh_token": refresh, "client_id": config.client_id}), True, repo.now()))
    return row["frontend_origin"]


def access_token(db):
    config = OAuthConfig.load()
    connection = get_connection(db)
    if not connection or not connection["enabled"] or not connection["encrypted_credentials"]:
        raise GmailOAuthError("Conecta Gmail desde Ajustes antes de importar alertas.")
    stored = decrypt(connection["encrypted_credentials"])
    if stored.get("client_id") != config.client_id:
        raise GmailOAuthError("El cliente OAuth no coincide con la conexión guardada. Usa el mismo cliente o vuelve a conectar Gmail.")
    db.conn.commit()
    try:
        credentials = Credentials(None, refresh_token=stored["refresh_token"], token_uri="https://oauth2.googleapis.com/token",
                                  client_id=config.client_id, client_secret=config.client_secret, scopes=[SCOPE])
        credentials.refresh(GoogleRequest())
        return credentials.token
    except Exception:
        raise GmailOAuthError("La autorización de Gmail caducó, fue revocada o no se pudo renovar. Vuelve a conectar desde Ajustes.") from None


def check_label(db):
    row = get_connection(db)
    token = access_token(db)
    labels = gmail_get(token, "labels").get("labels", [])
    return any(label.get("name") == row["folder"] for label in labels)


def disconnect(db):
    row = get_connection(db)
    refresh = None
    if row and row["encrypted_credentials"]:
        try:
            refresh = decrypt(row["encrypted_credentials"]).get("refresh_token")
        except GmailOAuthError:
            pass
    with db.transaction():
        db.execute("insert into gmail_connections (id, enabled) values (?, ?) on conflict (id) do update set enabled = excluded.enabled, encrypted_credentials = null, email = ''", ("gmail", False))
        db.execute("delete from gmail_oauth_states")
    if refresh:
        try:
            return requests.post("https://oauth2.googleapis.com/revoke", data={"token": refresh}, timeout=15).status_code == 200
        except Exception:
            return False
    return row is None or not row.get("encrypted_credentials")
