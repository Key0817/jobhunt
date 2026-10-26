from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

from . import repo
from .config import database_url, load_dotenv, load_ranking_profile, load_search_config
from .db import Database
from .models import STATUSES
from .pipeline import collect
from .sources import build_sources

log = logging.getLogger("jobbot")


def _wait_for_service(url: str, process: subprocess.Popen[bytes], name: str) -> None:
    deadline = time.monotonic() + 30
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        return_code = process.poll()
        if return_code is not None:
            raise RuntimeError(f"{name} terminó durante el inicio (código {return_code}).")
        try:
            with urllib.request.urlopen(url, timeout=1):
                return
        except OSError as exc:
            last_error = exc
            time.sleep(0.2)
    raise RuntimeError(
        f"{name} no respondió en 30 segundos ({last_error})."
    ) from last_error


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _serve() -> int:
    project_root = Path(__file__).resolve().parents[2]
    frontend_dir = project_root / "frontend"
    vite_cli = frontend_dir / "node_modules" / "vite" / "bin" / "vite.js"
    node = shutil.which("node")

    if node is None:
        raise RuntimeError("No se encontró Node.js. Instala Node.js para ejecutar el frontend.")
    if not vite_cli.is_file():
        raise RuntimeError(
            "No se encontraron las dependencias del frontend. Ejecuta `npm ci --prefix frontend`."
        )

    api_url = "http://127.0.0.1:8000"
    frontend_url = "http://127.0.0.1:5173"
    processes: list[subprocess.Popen[bytes]] = []
    try:
        api_process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "jobbot.api:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
            ],
            cwd=project_root,
        )
        processes.append(api_process)
        _wait_for_service(f"{api_url}/openapi.json", api_process, "La API")

        frontend_process = subprocess.Popen(
            [node, str(vite_cli), "--host", "127.0.0.1", "--port", "5173", "--strictPort"],
            cwd=frontend_dir,
        )
        processes.append(frontend_process)
        _wait_for_service(frontend_url, frontend_process, "El frontend")

        print(f"JobHunt está disponible en {frontend_url}", flush=True)
        if not webbrowser.open_new_tab(frontend_url):
            print(f"No se pudo abrir el navegador automáticamente. Visita {frontend_url}.")

        while True:
            for process, name in ((api_process, "La API"), (frontend_process, "El frontend")):
                return_code = process.poll()
                if return_code is not None:
                    raise RuntimeError(f"{name} terminó inesperadamente (código {return_code}).")
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nCerrando JobHunt...", flush=True)
        return 0
    finally:
        for process in reversed(processes):
            _stop_process(process)


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
    sub.add_parser("serve", help="Inicia el API y el frontend, y abre el navegador")
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
    if args.cmd == "serve":
        try:
            return _serve()
        except RuntimeError as exc:
            print(f"Error iniciando JobHunt: {exc}", file=sys.stderr)
            return 1

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
            return 1 if any(s.status != "success" for s in summaries) else 0
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