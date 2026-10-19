
from jobbot.cv.generator import generate_cv


if __name__ == "__main__":
    result = generate_cv(
        job_id="demo-fullstack",
        job_title="Junior Full Stack Developer",
        description="React, TypeScript, Next.js y PostgreSQL",
    )

    print("CV generado correctamente")
    print(f"Perfil seleccionado: {result['profile_key']}")
    print(f"Estado: {result['status']}")
    print(f"Archivo: {result['file_path']}")
