"""Private Supabase object storage; credentials never reach the browser."""
import os
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


class StorageError(RuntimeError):
    pass


def enabled():
    return os.environ.get('DOCUMENT_STORAGE', 'local') == 'supabase'


def verify_private_bucket():
    import json
    base = os.environ.get('SUPABASE_URL', '').rstrip('/')
    key = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')
    bucket = quote(os.environ.get('SUPABASE_STORAGE_BUCKET', 'jobhunt-private'), safe='')
    try:
        with urlopen(Request(base + '/storage/v1/bucket/' + bucket,
                            headers={'apikey': key, 'Authorization': 'Bearer ' + key}), timeout=15) as response:
            metadata = json.loads(response.read(100001))
        if not isinstance(metadata, dict) or metadata.get('public') is not False:
            raise StorageError('El bucket de documentos debe ser privado.')
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        raise StorageError('No se pudo verificar el bucket privado de documentos.') from None


def reference(key):
    return 'storage://' + os.environ.get('SUPABASE_STORAGE_BUCKET', 'jobhunt-private') + '/' + key


def _request(ref, data=None, content_type=None):
    parts = urlsplit(ref)
    bucket = os.environ.get('SUPABASE_STORAGE_BUCKET', 'jobhunt-private')
    if parts.scheme != 'storage' or parts.netloc != bucket or not parts.path or any(p in {'.', '..'} for p in parts.path.split('/')) or parts.query or parts.fragment:
        raise StorageError('Referencia de documento no permitida.')
    base = os.environ.get('SUPABASE_URL', '').rstrip('/')
    key = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')
    if not base.startswith('https://') or not key:
        raise StorageError('Configura el almacenamiento privado del servidor.')
    url = base + '/storage/v1/object/' + quote(bucket, safe='') + '/' + quote(parts.path.lstrip('/'), safe='/')
    headers = {'apikey': key, 'Authorization': 'Bearer ' + key}
    if data is not None:
        if len(data) > 20_000_000:
            raise StorageError('El documento supera el límite de 20 MB.')
        headers.update({'Content-Type': content_type or 'application/octet-stream', 'x-upsert': 'false'})
    try:
        with urlopen(Request(url, data=data, headers=headers, method='POST' if data is not None else 'GET'), timeout=30) as response:
            body = response.read(20_000_001)
            if len(body) > 20_000_000:
                raise StorageError('El documento supera el límite de 20 MB.')
            return body
    except HTTPError as exc:
        # Storage can return HTTP 400 with an embedded 404 for missing objects.
        import json
        try:
            missing = str(json.loads(exc.read(4096)).get('statusCode')) == '404'
        except (ValueError, AttributeError):
            missing = False
        if exc.code == 404 or missing:
            raise FileNotFoundError('No se encuentra el documento privado.') from None
        raise StorageError('El almacenamiento rechazó la operación. Revisa el bucket y las credenciales del servidor.') from None
    except (URLError, TimeoutError, OSError):
        raise StorageError('No se pudo conectar con el almacenamiento privado.') from None


def read(ref):
    return _request(ref)


def write(key, data, content_type):
    ref = reference(key)
    _request(ref, data, content_type)
    return ref
