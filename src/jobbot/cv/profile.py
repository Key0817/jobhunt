from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[3]
PROFILE_PATH = PROJECT_ROOT / "config" / "profile.yml"


def load_profile() -> dict:
    """Carga el perfil maestro privado desde config/profile.yml."""
    if not PROFILE_PATH.is_file():
        raise FileNotFoundError(
            f"No existe {PROFILE_PATH}. "
            "Crea config/profile.yml a partir de config/profile.example.yml."
        )

    with PROFILE_PATH.open("r", encoding="utf-8") as file:
        profile = yaml.safe_load(file)

    if not isinstance(profile, dict) or "profile" not in profile:
        raise ValueError("El archivo de perfil no contiene la sección 'profile'.")

    data = profile["profile"]
    if not isinstance(data, dict):
        raise ValueError("La sección 'profile' debe ser un objeto YAML.")

    if not data.get("experience") and not data.get("skills"):
        raise ValueError("Define experiencia o habilidades reales en el perfil para generar con IA.")

    return data
