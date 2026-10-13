from __future__ import annotations

import argparse
import logging
import sys

from . import repo
from .config import database_url, load_dotenv, load_search_config
from .db import Database
from .models import STATUSES
from .pipeline import collect
from .sources import build_sources


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobbot")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db", help="Crea las tablas (y activa RLS si es Postgres)")
    c = sub.add_parser("collect", help="Busca ofertas en las fuentes configuradas")
    c.add_argument("--sources", help="Lista separada por comas; por defecto, config/search.yml")
    ls = sub.add_parser("list", help="Lista las ofertas guardadas")
    ls.add_argument("--status", choices=STATUSES)
    ls.add_argument("--limit", type=int, default=30)
    st = sub.add_parser("status", help="Cambia el estado de una oferta")
    st.add_argument("job_id", help="Id completo o prefijo único")
    st.add_argument("new_status", choices=STATUSES)
    args = parser.parse_args(argv)

    # En Windows la terminal puede usar una codificación que no soporta emojis ni
    # algunos símbolos de los títulos reales; así evitamos que el programa se caiga al imprimir.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    load_dotenv()
    db = Database(database_url())
    try:
        if args.cmd == "init-db":
            db.init_schema()
            print("Base de datos lista.")
        elif args.cmd == "collect":
            query, configured = load_search_config()
            names = args.sources.split(",") if args.sources else configured
            summaries = collect(db, build_sources(names), query)
            return 1 if any(s.error for s in summaries) else 0
        elif args.cmd == "list":
            for j in repo.list_jobs(db, args.status, args.limit):
                print(f"{j['id'][:8]}  {j['status']:<11} {j['country'] or '--':<3} "
                      f"{j['company'][:22]:<22} {j['title'][:50]}")
        elif args.cmd == "status":
            try:
                job_id = repo.resolve_job_id(db, args.job_id)
                repo.change_status(db, job_id, args.new_status)
            except (LookupError, ValueError) as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            print(f"{job_id[:8]} -> {args.new_status}")
    finally:
        db.close()
    return 0