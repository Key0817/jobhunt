"""API del dashboard. Inicia el API y el frontend con `python -m jobbot serve`."""
from pathlib import Path
from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from . import repo
from .config import database_url, load_dotenv
from .db import Database
from .schemas import JobDetail, JobResponse, JobStatus, JobUpdateStatus, StatsResponse
from .cv.generator import generate_cv, OUTPUT_DIR

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

@app.get("/api/jobs", response_model=list[JobResponse])
def list_jobs(
    status: Optional[JobStatus] = None,
    min_score: int = Query(0, ge=0, le=100),
    limit: int = Query(200, ge=1, le=1000),
    sort: str = Query("score", pattern="^(score|recent)$"),
    db: Database = Depends(get_db),
):
    """Ofertas para la tabla y el Kanban. Por defecto, las de mejor puntaje primero."""
    return repo.list_jobs(db, status=status.value if status else None, limit=limit,
                          sort=sort, min_score=min_score)


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

    try:
        result = generate_cv(
            job_id=job["id"],
            job_title=job["title"],
            description=job.get("description") or "",
            db=db,
        )
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
