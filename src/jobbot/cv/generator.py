
from pathlib import Path

from docxtpl import DocxTemplate

from .profile import PROJECT_ROOT, load_profile
from .selectors import select_base_profile
from .. import repo
from ..db import Database


TEMPLATE_PATH = PROJECT_ROOT / "templates" / "cv_master.docx"
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
) -> dict:
    """Genera el DOCX y, si recibe db, registra la versión pendiente de revisión."""
    if not TEMPLATE_PATH.is_file():
        raise FileNotFoundError(f"No existe la plantilla: {TEMPLATE_PATH}")

    profile = load_profile()
    profile_key, selected_text = select_base_profile(
        job_title,
        description,
        profile["base_profiles"],
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    from datetime import datetime

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = (
        f"{safe_filename(job_id)}_"
        f"{safe_filename(profile_key)}_{timestamp}.docx"
    )
    output_path = OUTPUT_DIR / filename

    doc = DocxTemplate(str(TEMPLATE_PATH))
    doc.render({"perfil": selected_text})
    doc.save(str(output_path))

    result = {
        "job_id": job_id,
        "profile_key": profile_key,
        "profile_text": selected_text,
        "file_path": str(output_path),
        "status": "pending_review",
        "version_id": None,
    }

    if db is not None:
        relative_path = output_path.relative_to(PROJECT_ROOT).as_posix()
        version = repo.save_cv_version(
            db=db,
            job_id=job_id,
            variant=profile_key,
            profile_text=selected_text,
            file_path=relative_path,
            profile_key=profile_key,
            model_name=None,
        )
        result["version_id"] = version["id"]

    return result
