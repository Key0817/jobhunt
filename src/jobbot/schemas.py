"""Formas de los datos que entran y salen de la API."""
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from .models import STATUSES, CV_STATUSES

# Se arma desde models.STATUSES para que la API y la base de datos nunca se desincronicen.
JobStatus = Enum("JobStatus", {status: status for status in STATUSES}, type=str)


CVStatus = Enum("CVStatus", {status: status for status in CV_STATUSES}, type=str)


class CVUpdateStatus(BaseModel):
    status: CVStatus


class JobOrigin(BaseModel):
    source: str
    label: str
    url: str


class JobResponse(BaseModel):
    id: str
    title: str
    company: str
    status: JobStatus
    score: float
    url: str
    source: str
    source_display: str = ""
    origins: list[JobOrigin] = Field(default_factory=list)
    remote: Optional[bool] = None
    detected_at: Optional[datetime] = None
    description_incomplete: bool = False
    location_needs_review: bool = False
    country: Optional[str] = None
    location: Optional[str] = None
    posted_at: Optional[datetime] = None
    applied_at: Optional[datetime] = None


class JobDetail(JobResponse):
    description: str = ""
    notes: str = ""
    remote_modality: Optional[str] = None
    reasons: list[str] = []


class JobUpdateStatus(BaseModel):
    status: JobStatus


class WeeklyCount(BaseModel):
    week: str
    count: int


class StatsResponse(BaseModel):
    total_jobs: int
    new_last_7_days: int
    applied: int
    interviews: int
    offers: int
    by_status: dict[str, int]
    weekly_applications: list[WeeklyCount]