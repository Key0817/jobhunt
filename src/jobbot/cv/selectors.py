
def select_profile_key(job_title: str, description: str = "") -> str:
    text = f"{job_title} {description}".casefold()

    if any(term in text for term in (
        "soporte técnico", "technical support", "help desk",
        "helpdesk", "service desk", "it support",
    )):
        return "soporte_it"

    if any(term in text for term in (
        ".net", "backend", "back-end", "back end",
        "c#", "asp.net", "entity framework",
    )):
        return "backend_dotnet"

    if any(term in text for term in (
        "frontend", "front-end", "front end", "ui developer",
    )):
        return "frontend"

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
