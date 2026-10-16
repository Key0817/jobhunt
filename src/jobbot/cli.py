from __future__ import annotations

import argparse
import json
import logging
import sys

from . import repo
from .config import database_url, load_dotenv, load_ranking_profile, load_search_config
from .db import Database
from .models import STATUSES
from .pipeline import collect
from .sources import build_sources

log = logging.getLogger("jobbot")


def _score_summary(db: Database) -> str:
    rows = db.query("select score from jobs")
    scores = [float(r["score"]) for r in rows]
    high = sum(s >= 70 for s in scores)
    mid = sum(40 <= s < 70 for s in scores)
    return f"puntaje >= 70: {high} | entre 40 y 69: {mid} | menos de 40: {len(scores) - high - mid}"


def _show(db: Database, job: dict) -> None:
    raw = json.loads(job["raw"] or "{}")
    print(f"{job['title']}")
    print(f"  Empresa:    {job['company'] or '-'}")
    print(f"  Ubicación:  {job['location'] or '-'} | modalidad: {raw.get('remote_modality') or '-'}")
    print(f"  Fuente:     {job['source']} | publicada: {job['posted_at'] or '-'}")
    print(f"  Estado:     {job['status']}")
    print(f"  URL:        {job['url']}")
    print(f"  Puntaje:    {int(job['score'])}")
    reasons = json.loads(job.get("score_detail") or "{}").get("reasons", [])
    if reasons:
        for reason in reasons:
            print(f"     {reason}")
    else:
        print("     (sin detalle: corre `python -m jobbot rank`)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobbot")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db", help="Crea las tablas (y activa RLS si es Postgres)")
    c = sub.add_parser("collect", help="Busca ofertas en las fuentes configuradas")
    c.add_argument("--sources", help="Lista separada por comas; por defecto, config/search.yml")
    sub.add_parser("rank", help="Recalcula el puntaje de todas las ofertas con config/ranking.yml")
    ls = sub.add_parser("list", help="Lista las ofertas guardadas")
    ls.add_argument("--status", choices=STATUSES)
    ls.add_argument("--limit", type=int, default=30)
    ls.add_argument("--sort", choices=("score", "recent"), default="score")
    ls.add_argument("--min-score", type=int, default=0)
    sh = sub.add_parser("show", help="Muestra una oferta y por qué tiene ese puntaje")
    sh.add_argument("job_id", help="Id completo o prefijo único")
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
            return 0
        db.migrate()  # agrega columnas nuevas si la base es de una versión anterior

        if args.cmd == "collect":
            query, configured = load_search_config()
            names = args.sources.split(",") if args.sources else configured
            profile = load_ranking_profile(query.keywords)
            if profile is None:
                log.warning("No se encontró config/ranking.yml: las ofertas nuevas quedan sin puntaje")
            summaries = collect(db, build_sources(names), query, profile)
            return 1 if any(s.error for s in summaries) else 0
        elif args.cmd == "rank":
            query, _ = load_search_config()
            profile = load_ranking_profile(query.keywords)
            if profile is None:
                print("Error: no existe config/ranking.yml", file=sys.stderr)
                return 1
            total = repo.rerank_all(db, profile)
            print(f"Ofertas recalculadas: {total}")
            print(_score_summary(db))
        elif args.cmd == "list":
            for j in repo.list_jobs(db, args.status, args.limit, args.sort, args.min_score):
                print(f"{j['id'][:8]}  {int(j['score']):>3}  {j['status']:<11} "
                      f"{j['company'][:22]:<22} {j['title'][:50]}")
        elif args.cmd == "show":
            try:
                job = repo.get_job(db, repo.resolve_job_id(db, args.job_id))
            except LookupError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            _show(db, job)
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