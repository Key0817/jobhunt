
from pathlib import Path
import os
from io import BytesIO

from docxtpl import DocxTemplate

from .profile import PROJECT_ROOT, load_profile
from .ai import generate_profile
from .. import repo
from ..db import Database
from .. import storage


TEMPLATE_PATH = PROJECT_ROOT / "templates" / "CV_Master.docx"
OUTPUT_DIR = PROJECT_ROOT / "generated"


def safe_filename(value: str) -> str:
    import re

    value = re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_")
    return value[:60] or "oferta"


def generate_cv(
    job_id: str,
    job_title: str,
    description: str = "",
    db: Database | None = None,
    company: str = "",
) -> dict:
    """Genera el DOCX y, si recibe db, registra la versión pendiente de revisión."""
    template_path = Path(os.environ.get('CV_TEMPLATE_PATH') or str(TEMPLATE_PATH))
    template = BytesIO(storage.read(storage.reference(os.environ['CV_TEMPLATE_OBJECT_KEY']))) if os.environ.get('CV_TEMPLATE_OBJECT_KEY') else template_path
    if not isinstance(template, BytesIO) and not template_path.is_file():
        raise FileNotFoundError(f"No existe la plantilla: {TEMPLATE_PATH}")

    profile = load_profile()
    generated = generate_profile(profile, job_title, description, company)
    profile_key = "ai"
    selected_text = generated.text

    if not storage.enabled():
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    from datetime import datetime

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    filename = (
        f"{safe_filename(job_id)}_"
        f"{safe_filename(profile_key)}_{timestamp}.docx"
    )
    output_path = OUTPUT_DIR / filename

    doc = DocxTemplate(template if isinstance(template, BytesIO) else str(template))
    doc.render({"perfil": selected_text}, autoescape=True)
    if storage.enabled():
        buffer = BytesIO()
        doc.save(buffer)
        stored_path = storage.write('cvs/' + filename, buffer.getvalue(), 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    else:
        doc.save(str(output_path))
        stored_path = str(output_path)

    result = {
        "job_id": job_id,
        "profile_key": profile_key,
        "profile_text": selected_text,
        "model_name": generated.model_name,
        "file_path": stored_path,
        "status": "pending_review",
        "version_id": None,
    }

    if db is not None:
        relative_path = stored_path if storage.enabled() else output_path.relative_to(PROJECT_ROOT).as_posix()
        version = repo.save_cv_version(
            db=db,
            job_id=job_id,
            variant=profile_key,
            profile_text=selected_text,
            file_path=relative_path,
            profile_key=profile_key,
            model_name=generated.model_name,
        )
        result["version_id"] = version["id"]

    return result
