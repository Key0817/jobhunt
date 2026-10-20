
def select_profile_key(job_title: str, description: str = "") -> str:
    # El título tiene más peso que las tecnologías mencionadas
    # en la descripción de la oferta.
    title = job_title.casefold()
    description_text = description.casefold()

    # Primero identificamos el área principal del puesto.
    if any(term in title for term in (
        "soporte técnico", "technical support", "help desk",
        "helpdesk", "service desk", "it support",
    )):
        return "soporte_it"

    if any(term in title for term in (
        "frontend", "front-end", "front end",
        "front end developer", "ui developer",
        "front-end software engineer",
    )):
        return "frontend"

    if any(term in title for term in (
        ".net", "backend", "back-end", "back end",
        "c#", "asp.net", "entity framework",
    )):
        return "backend_dotnet"

    # Si el título no permite determinar el área, usamos
    # la descripción como señal secundaria.
    if any(term in description_text for term in (
        "soporte técnico", "technical support", "help desk",
        "helpdesk", "service desk", "it support",
    )):
        return "soporte_it"

    if any(term in description_text for term in (
        "frontend", "front-end", "front end",
        "ui developer", "react developer",
    )):
        return "frontend"

    if any(term in description_text for term in (
        ".net", "backend", "back-end", "back end",
        "c#", "asp.net", "entity framework",
    )):
        return "backend_dotnet"

    return "full_stack"


def select_base_profile(
    job_title: str,
    description: str,
    base_profiles: dict[str, str],
) -> tuple[str, str]:
    key = select_profile_key(job_title, description)

    if key not in base_profiles:
        key = "full_stack"

    if key not in base_profiles:
        raise ValueError("No existe un perfil base de Full Stack.")

    return key, base_profiles[key]
