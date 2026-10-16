# api.py
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional
from .schemas import JobResponse, JobUpdateStatus, JobStatus, StatsResponse
from . import repo
app = FastAPI(title="JobHunt API", version="1.0.0")

# Habilitar CORS para que el frontend en Vite pueda consumir la API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/jobs", response_model=List[JobResponse])
def list_jobs(
    status: Optional[JobStatus] = None,
    min_score: Optional[int] = Query(None, ge=0, le=100),
    modality: Optional[str] = None
):
    """Obtiene la lista de ofertas filtrada para la vista de Tabla y Kanban."""
    return repo.get_jobs(status=status, min_score=min_score, modality=modality)

@app.get("/api/jobs/{job_id}", response_model=JobResponse)
def get_job_detail(job_id: int):
    """Obtiene el detalle completo de una oferta específica."""
    job = repo.get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Oferta no encontrada")
    return job

@app.patch("/api/jobs/{job_id}/status", response_model=JobResponse)
def update_job_status(job_id: int, update_data: JobUpdateStatus):
    """
    Actualiza el estado de una oferta (ej. al arrastrar en Kanban).
    El repo DEBE registrar automáticamente este cambio en 'status_history'.
    """
    updated_job = repo.update_status(job_id, update_data.status.value, update_data.notes)
    if not updated_job:
        raise HTTPException(status_code=404, detail="Oferta no encontrada")
    return updated_job

@app.get("/api/stats", response_model=StatsResponse)
def get_stats():
    """Retorna los datos agregados para las tarjetas superiores del dashboard."""
    return repo.get_dashboard_stats()