"""One-time, preview-first import of existing local CVs into private Storage."""
import argparse
import hashlib
from pathlib import Path

from . import storage
from .config import load_dotenv, database_url
from .db import Database
from .cv.profile import PROJECT_ROOT


def migrate(db, apply=False):
    counts = {'pending': 0, 'migrated': 0, 'remote': 0, 'missing': 0}
    rows = db.query('select id, file_path from cv_versions')
    db.conn.commit()
    for row in rows:
        value = row.get('file_path') or ''
        if value.startswith('storage://'):
            counts['remote'] += 1
            continue
        path = (PROJECT_ROOT / value).resolve()
        if not value or not path.is_relative_to((PROJECT_ROOT / 'generated').resolve()) or path.suffix.lower() != '.docx' or not path.is_file():
            counts['missing'] += 1
            continue
        counts['pending'] += 1
        if not apply:
            continue
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        key = 'cvs/imported/' + row['id'] + '-' + digest[:16] + '.docx'
        ref = storage.reference(key)
        try:
            existing = storage.read(ref)
        except FileNotFoundError:
            ref = storage.write(key, content, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
        else:
            if hashlib.sha256(existing).hexdigest() != digest:
                raise storage.StorageError('El documento importado no coincide con el original.')
        with db.transaction():
            db.execute('update cv_versions set file_path = ? where id = ? and file_path = ?', (ref, row['id'], value))
        counts['migrated'] += 1
    return counts


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Sube documentos y actualiza referencias; por defecto solo cuenta.')
    args = parser.parse_args()
    db = Database(database_url())
    try:
        if args.apply:
            storage.verify_private_bucket()
        print(migrate(db, args.apply))
    finally:
        db.close()


if __name__ == '__main__':
    main()
