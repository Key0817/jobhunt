# api.py
from fastapi import FastAPI, HTTPException, Query, Depends
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional
from .schemas import JobResponse, JobUpdateStatus, JobStatus, StatsResponse
from . import repo
from .db import Database 

app = FastAPI(title="JobHunt API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Dependencia para inyectar la base de datos en cada petición
# Ajusta esto según cómo inicialices tu base de datos en tu proyecto
def get_db():
    db = Database("DATABASE_URL") 
    try:
        yield db
    finally:
        pass # Si tu db necesita ser cerrada, hazlo aquí (ej: db.close())

@app.get("/api/jobs", response_model=List[JobResponse])
async def list_jobs(
    status: Optional[str] = None, 
    min_score: Optional[int] = Query(0, ge=0, le=100),
    db: Database = Depends(get_db)
):
    """Obtiene la lista de ofertas filtrada para la vista de Tabla y Kanban."""
    return await repo.list_jobs(db, status=status, min_score=min_score)

@app.get("/api/jobs/{job_id}", response_model=JobResponse)
async def get_job_detail(job_id: str, db: Database = Depends(get_db)):
    """Obtiene el detalle completo de una oferta específica."""
    try:
        job = await repo.get_job(db, job_id)
        return job
    except LookupError:
        raise HTTPException(status_code=404, detail="Oferta no encontrada")

@app.patch("/api/jobs/{job_id}/status", response_model=dict)
async def update_job_status(job_id: str, update_data: JobUpdateStatus, db: Database = Depends(get_db)):
    """Actualiza el estado de una oferta (ej. al arrastrar en Kanban)."""
    try:
        await repo.change_status(db, job_id, update_data.status.value)
        return {"message": "Estado actualizado correctamente"}
    except LookupError:
        raise HTTPException(status_code=404, detail="Oferta no encontrada")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/stats", response_model=StatsResponse)
async def get_stats(db: Database = Depends(get_db)):
    """Retorna los datos agregados para las tarjetas superiores del dashboard."""
    # Nota: No tienes una función get_dashboard_stats en tu repo.py actual.
    # Por ahora devuelvo datos simulados para que el frontend no se rompa.
    return {
        "total_jobs": 24,
        "interviews": 5,
        "offers": 2,
        "applied": 12
    }