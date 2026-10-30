"""API del dashboard. Inicia el API y el frontend con `python -m jobbot serve`."""
from pathlib import Path
from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Optional
from typing import Literal
from io import BytesIO
from fastapi.responses import StreamingResponse

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from . import repo
from .config import database_url, load_dotenv
from .db import Database
from .schemas import JobDetail, JobResponse, JobStatus, JobUpdateStatus, StatsResponse, CVUpdateStatus, JobPage
from .cv.generator import generate_cv, OUTPUT_DIR
from .cv.ai import AIError, generate_cover_letter
from .cv.profile import load_profile
from .gmail_routes import create_router as gmail_router
from .auth import require_user
from .deployment import production, frontend_origins, validate_production
from .storage import StorageError
from .cv.pdf import cv_pdf_bytes, PDFConversionError
from fastapi.responses import JSONResponse

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_production()
    if production():
        from .storage import verify_private_bucket
        verify_private_bucket()
    db = Database(database_url())
    try:
        db.migrate()
    finally:
        db.close()
    yield


app = FastAPI(title="JobHunt API", version="1.0.0", lifespan=lifespan, dependencies=[Depends(require_user)],
              docs_url=None if production() else "/docs", redoc_url=None if production() else "/redoc",
              openapi_url=None if production() else "/openapi.json")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get('/api/auth/me')
def authenticated_user(request: Request):
    return {'id': getattr(request.state, 'user_id', None)}


@app.exception_handler(StorageError)
async def storage_problem(request, error):
    return JSONResponse({"detail": str(error)}, status_code=503, headers={"Cache-Control": "no-store"})

app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(frontend_origins()),
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


@app.get("/api/jobs/page", response_model=JobPage)
def jobs_page(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    status: Optional[JobStatus] = None,
    min_score: int = Query(0, ge=0, le=100),
    sort: Literal["score", "recent"] = "score",
    source: Optional[str] = Query(None, max_length=100, pattern="^[A-Za-z0-9_:-]+$"),
    modality: Literal["all", "remote", "onsite", "unknown"] = "all",
    days: Optional[int] = Query(None, ge=1, le=3650),
    db: Database = Depends(get_db),
):
    return repo.paginated_jobs(db, page=page, page_size=page_size,
                              status=status.value if status else None, sort=sort,
                              min_score=min_score, source=source, modality=modality, days=days)


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
        "file_path": result["file_path"] if result["file_path"].startswith("storage://") else Path(result["file_path"]).relative_to(OUTPUT_DIR.parent).as_posix(),
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

    try:
        pdf_bytes = cv_pdf_bytes(stored_path)
    except FileNotFoundError as exc:
        raise HTTPException(404, "No se encontró el archivo DOCX de esta versión.") from exc
    except PDFConversionError as exc:
        raise HTTPException(500, str(exc)) from exc

    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Cache-Control": "no-store",
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
