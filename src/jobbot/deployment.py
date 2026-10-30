"""Explicit settings for the single-owner deployment."""
import os
from urllib.parse import urlsplit


def production():
    return os.environ.get('APP_ENV', '').lower() == 'production' or os.environ.get('RENDER') == 'true'


def auth_required():
    return production() or os.environ.get('AUTH_REQUIRED', '').lower() == 'true'


def frontend_origins():
    value = os.environ.get('FRONTEND_ORIGINS', 'http://localhost:5173,http://127.0.0.1:5173' if not production() else '')
    return {origin.strip().rstrip('/') for origin in value.split(',') if origin.strip()}


def validate_production():
    if not production():
        return
    names = ('DATABASE_URL', 'SUPABASE_URL', 'SUPABASE_ANON_KEY', 'APP_OWNER_ID',
             'SUPABASE_SERVICE_ROLE_KEY', 'FRONTEND_ORIGINS', 'GMAIL_OAUTH_REDIRECT_URI')
    missing = [name for name in names if not os.environ.get(name, '').strip()]
    if missing:
        raise RuntimeError('Configuración de producción incompleta: ' + ', '.join(missing))
    if not os.environ['DATABASE_URL'].startswith(('postgresql://', 'postgres://')):
        raise RuntimeError('Producción requiere PostgreSQL persistente.')
    base = urlsplit(os.environ['SUPABASE_URL'])
    if base.scheme != 'https' or not base.hostname or base.username or base.password or base.path not in ('', '/') or base.query or base.fragment:
        raise RuntimeError('SUPABASE_URL debe ser el origen HTTPS del proyecto.')
    for origin in frontend_origins():
        parts = urlsplit(origin)
        if parts.scheme != 'https' or not parts.hostname or parts.path or parts.query or parts.fragment or parts.username:
            raise RuntimeError('FRONTEND_ORIGINS debe contener orígenes HTTPS exactos.')
    if not frontend_origins():
        raise RuntimeError('Configura al menos un origen permitido.')
    callback = urlsplit(os.environ['GMAIL_OAUTH_REDIRECT_URI'])
    if callback.scheme != 'https' or not callback.hostname or callback.path != '/api/settings/gmail/callback' or callback.query or callback.fragment or callback.username or callback.password:
        raise RuntimeError('GMAIL_OAUTH_REDIRECT_URI debe ser un callback HTTPS del backend.')
    if os.environ.get('DOCUMENT_STORAGE', 'local') != 'supabase':
        raise RuntimeError('Producción requiere DOCUMENT_STORAGE=supabase.')
    for remote, local in (('PROFILE_OBJECT_KEY', 'PROFILE_PATH'), ('CV_TEMPLATE_OBJECT_KEY', 'CV_TEMPLATE_PATH')):
        if not os.environ.get(remote) and not os.environ.get(local):
            raise RuntimeError('Configura ' + remote + ' o ' + local + ' como recurso privado.')
