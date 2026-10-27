"""API del dashboard. Inicia el API y el frontend con `python -m jobbot serve`."""
from pathlib import Path
from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Optional
import subprocess
import tempfile
from io import BytesIO
from fastapi.responses import StreamingResponse

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from . import repo
from .config import database_url, load_dotenv
from .db import Database
from .schemas import JobDetail, JobResponse, JobStatus, JobUpdateStatus, StatsResponse, CVUpdateStatus
from .cv.generator import generate_cv, OUTPUT_DIR
from .cv.ai import AIError, generate_cover_letter
from .cv.profile import load_profile
from .gmail_routes import create_router as gmail_router

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = Database(database_url())
    try:
        db.migrate()
    finally:
        db.close()
    yield


app = FastAPI(title="JobHunt API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db() -> Iterator[Database]:
    """Una conexión por petición, cerrada al terminar."""
    db = Database(database_url())
    try:
        yield db
    finally:
        db.close()

app.include_router(gmail_router(get_db))


@app.get("/api/jobs", response_model=list[JobResponse])
def list_jobs(
    status: Optional[JobStatus] = None,
    min_score: int = Query(0, ge=0, le=100),
    limit: int = Query(200, ge=1, le=1000),
    sort: str = Query("score", pattern="^(score|recent)$"),
    source: Optional[str] = Query(None, max_length=100, pattern="^[A-Za-z0-9_:-]+$"),
    db: Database = Depends(get_db),
):
    """Ofertas para la tabla y el Kanban. Por defecto, las de mejor puntaje primero."""
    return repo.list_jobs(db, status=status.value if status else None, limit=limit,
                          sort=sort, min_score=min_score, source=source)


@app.get("/api/jobs/{job_id}", response_model=JobDetail)
def get_job_detail(job_id: str, db: Database = Depends(get_db)):
    """Detalle de una oferta: descripción completa y por qué tiene ese puntaje."""
    try:
        return repo.get_job_detail(db, job_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Oferta no encontrada")


@app.patch("/api/jobs/{job_id}/status", response_model=dict)
def update_job_status(job_id: str, update: JobUpdateStatus, db: Database = Depends(get_db)):
    """Cambia el estado de una oferta (queda en el historial)."""
    try:
        repo.change_status(db, job_id, update.status.value)
    except LookupError:
        raise HTTPException(status_code=404, detail="Oferta no encontrada")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"id": job_id, "status": update.status.value}


@app.get("/api/stats", response_model=StatsResponse)
def get_stats(db: Database = Depends(get_db)):
    """Números reales para las tarjetas y el gráfico del dashboard."""
    return repo.get_stats(db)

@app.post("/api/jobs/{job_id}/cv", response_model=dict, status_code=201)
def create_job_cv(job_id: str, db: Database = Depends(get_db)):
    """Genera un CV para una oferta y registra la versión para revisión."""
    try:
        job = repo.get_job_detail(db, job_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Oferta no encontrada")

    db.conn.commit()
    try:
        result = generate_cv(
            job_id=job["id"],
            job_title=job["title"],
            description=job.get("description") or "",
            db=db,
            company=job.get("company") or "",
        )
    except AIError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo generar el CV: {exc}",
        )
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return {
        "job_id": result["job_id"],
        "version_id": result["version_id"],
        "profile_key": result["profile_key"],
        "status": result["status"],
        "file_path": Path(result["file_path"]).relative_to(
            OUTPUT_DIR.parent
        ).as_posix(),
    }


@app.get("/api/jobs/{job_id}/cv", response_model=list[dict])
def get_job_cv_versions(job_id: str, db: Database = Depends(get_db)):
    """Lista las versiones de CV generadas para una oferta."""
    try:
        versions = repo.list_cv_versions(db, job_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Oferta no encontrada")
    return [
        {
            "id": version["id"],
            "job_id": version["job_id"],
            "variant": version["variant"],
            "profile_key": version.get("profile_key", ""),
            "status": version.get("status", "pending_review"),
            "approved": version.get("approved", False),
            "model_name": version.get("model_name"),
            "created_at": version["created_at"],
        }
        for version in versions
    ]



@app.get("/api/cvs", response_model=list[dict])
def list_all_cvs(db: Database = Depends(get_db)):
    """Lista todas las versiones de CV para la biblioteca."""
    versions = repo.list_all_cv_versions(db)

    return [
        {
            "id": version["id"],
            "job_id": version["job_id"],
            "job_title": version["job_title"],
            "company": version["company"],
            "variant": version["variant"],
            "profile_key": version.get("profile_key", ""),
            "status": version.get("status", "pending_review"),
            "approved": bool(version.get("approved", False)),
            "model_name": version.get("model_name"),
            "created_at": version["created_at"],
        }
        for version in versions
    ]

@app.get("/api/cvs/{version_id}")
def get_cv_detail(
    version_id: str,
    db: Database = Depends(get_db),
):
    """Devuelve el contenido editable de una versión del CV."""
    try:
        version = repo.get_cv_version_detail(db, version_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "id": version["id"],
        "job_id": version["job_id"],
        "job_title": version["job_title"],
        "company": version["company"],
        "variant": version["variant"],
        "profile_text": version["profile_text"],
        "cover_letter": version.get("cover_letter"),
        "cover_letter_model_name": version.get("cover_letter_model_name"),
        "profile_key": version.get("profile_key", ""),
        "status": version.get("status", "pending_review"),
        "approved": bool(version.get("approved", False)),
        "model_name": version.get("model_name"),
        "created_at": version["created_at"],
    }


@app.get("/api/cvs/{version_id}/pdf")
def download_cv_pdf(
    version_id: str,
    db: Database = Depends(get_db),
):
    try:
        cv = repo.get_cv_version_detail(db, version_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    stored_path = cv.get("file_path")

    if not stored_path:
        raise HTTPException(
            status_code=404,
            detail="Esta versión no tiene un archivo generado.",
        )

    # Las rutas de generator.py son relativas a PROJECT_ROOT.
    project_root = OUTPUT_DIR.parent.resolve()
    file_path = Path(stored_path)

    if not file_path.is_absolute():
        file_path = project_root / file_path

    file_path = file_path.resolve()

    # Evita acceder a archivos fuera del proyecto.
    if not file_path.is_relative_to(project_root):
        raise HTTPException(
            status_code=400,
            detail="Ruta de documento no permitida.",
        )

    if not file_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="No se encontró el archivo DOCX de esta versión.",
        )

    if file_path.suffix.lower() != ".docx":
        raise HTTPException(
            status_code=400,
            detail="El documento generado no es un archivo DOCX.",
        )

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            subprocess.run(
                [
                    "soffice",
                    "--headless",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    temp_dir,
                    str(file_path),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )

            pdf_path = Path(temp_dir) / f"{file_path.stem}.pdf"

            if not pdf_path.is_file():
                raise HTTPException(
                    status_code=500,
                    detail="LibreOffice no generó el archivo PDF.",
                )

            pdf_bytes = pdf_path.read_bytes()

    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=500,
            detail="LibreOffice no está instalado o soffice no está en el PATH.",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(
            status_code=500,
            detail="La conversión del CV excedió el tiempo permitido.",
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Error durante la conversión del DOCX: {exc.stderr}",
        ) from exc

    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="cv_{version_id}.pdf"'
            )
        },
    )

@app.patch("/api/cvs/{version_id}/status", response_model=dict)
def update_cv_status(version_id: str, update: CVUpdateStatus, db: Database = Depends(get_db)):
    try:
        saved = repo.change_cv_status(db, version_id, update.status.value)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "id": saved["id"], "status": saved["status"],
        "approved": bool(saved["approved"]), "approved_at": saved["approved_at"],
    }


@app.post("/api/cvs/{version_id}/cover-letter", response_model=dict)
def create_cover_letter(version_id: str, db: Database = Depends(get_db)):
    """Genera y guarda la carta para la oferta de esta versión del CV."""
    try:
        cv = repo.get_cv_version_detail(db, version_id)
        job = repo.get_job_detail(db, cv["job_id"])
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.conn.commit()
    try:
        generated = generate_cover_letter(load_profile(), cv, job)
    except AIError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    try:
        saved = repo.save_cover_letter(db, version_id, generated.text, generated.model_name)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "id": saved["id"], "cover_letter": saved["cover_letter"],
        "cover_letter_model_name": saved["cover_letter_model_name"],
    }


@app.get("/api/collection/email")
def email_collection_summary(db: Database = Depends(get_db)):
    from .mail_imports import collection_summary
    return collection_summary(db)
