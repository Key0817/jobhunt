"""Convert the generated Word document instead of rebuilding its layout."""

import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

from .profile import PROJECT_ROOT


class PDFConversionError(RuntimeError):
    pass


def find_libreoffice() -> str:
    configured = os.environ.get("LIBREOFFICE_PATH")
    candidates = [configured] if configured else [
        shutil.which("soffice"),
        shutil.which("libreoffice"),
        "C:/Program Files/LibreOffice/program/soffice.exe",
        "C:/Program Files (x86)/LibreOffice/program/soffice.exe",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise PDFConversionError(
        "Instala LibreOffice o configura LIBREOFFICE_PATH con la ruta a soffice."
    )


def cv_pdf_bytes(file_path: str) -> bytes:
    """Export the saved DOCX, using an isolated LibreOffice profile per request."""
    if file_path.startswith('storage://'):
        from .. import storage
        return convert_docx(storage.read(file_path))
    source = (PROJECT_ROOT / file_path).resolve()
    if not source.is_relative_to((PROJECT_ROOT / "generated").resolve()):
        raise PDFConversionError("El archivo del CV debe estar en generated/.")
    if source.suffix.lower() != ".docx" or not source.is_file():
        raise FileNotFoundError("No se encuentra el DOCX de esta versión del CV.")

    return convert_docx(source)


def convert_docx(content: bytes | Path) -> bytes:
    executable = find_libreoffice()
    with TemporaryDirectory(prefix="jobbot-pdf-") as temporary:
        directory = Path(temporary)
        if isinstance(content, Path):
            source = content
        else:
            source = directory / 'cv.docx'
            source.write_bytes(content)
        try:
            result = subprocess.run(
                [
                    executable,
                    f"-env:UserInstallation={(directory / 'profile').as_uri()}",
                    "--headless",
                    "--convert-to", "pdf:writer_pdf_Export",
                    "--outdir", str(directory),
                    str(source),
                ],
                capture_output=True,
                timeout=90,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PDFConversionError("No se pudo convertir el DOCX a PDF con LibreOffice.") from exc
        output = directory / f"{source.stem}.pdf"
        if result.returncode != 0 or not output.is_file():
            raise PDFConversionError("LibreOffice no pudo exportar el CV a PDF.")
        data = output.read_bytes()
        if not data.startswith(b"%PDF-"):
            raise PDFConversionError("La conversión no produjo un PDF válido.")
        return data
