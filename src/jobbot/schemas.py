# schemas.py
from pydantic import BaseModel, Field, HttpUrl
from typing import List, Optional, Dict
from datetime import datetime
from enum import Enum

class JobStatus(str, Enum):
    encontradas = "encontradas"
    aplicables = "aplicables"
    cv_listo = "cv_listo"
    solicitadas = "solicitadas"
    entrevista = "entrevista"
    oferta = "oferta"
    rechazada = "rechazada"
    descartada = "descartada"

class JobUpdateStatus(BaseModel):
    status: JobStatus
    notes: Optional[str] = Field(None, description="Notas adicionales al cambiar de estado")

class JobResponse(BaseModel):
    id: int
    title: str
    company: str
    modality: str
    url: str
    description: str
    score: int
    score_reasons: Optional[str] = None
    status: JobStatus
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True 

class StatsResponse(BaseModel):
    applications_this_week: int
    status_counts: Dict[JobStatus, int]