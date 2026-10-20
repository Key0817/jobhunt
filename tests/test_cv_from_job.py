
from jobbot.config import database_url, load_dotenv
from jobbot.db import Database
from jobbot import repo
from jobbot.cv.generator import generate_cv


def main():
    load_dotenv()

    db = Database(database_url())

    try:
        jobs = repo.list_jobs(
            db,
            limit=30,
            sort="score",
        )

        if not jobs:
            print("No hay ofertas guardadas en la base de datos.")
            print("Ejecuta primero la recolección de ofertas.")
            return

        print("\nOFERTAS DISPONIBLES")
        print("=" * 75)

        for index, job in enumerate(jobs, start=1):
            print(
                f"{index}. [{job['score']} puntos] "
                f"{job['title']} — {job['company'] or 'Empresa no indicada'}"
            )
            print(f"   ID: {job['id']}")
            print()

        while True:
            selection = input(
                "Selecciona el número de la oferta para generar el CV "
                "(0 para cancelar): "
            ).strip()

            if selection == "0":
                print("Operación cancelada.")
                return

            if selection.isdigit() and 1 <= int(selection) <= len(jobs):
                job = jobs[int(selection) - 1]
                break

            print("Selección inválida. Inténtalo de nuevo.")

        # Recuperamos los datos completos de la oferta elegida.
        detail = repo.get_job_detail(db, job["id"])

        print("\nOFERTA SELECCIONADA")
        print("=" * 75)
        print(f"Título: {detail['title']}")
        print(f"Empresa: {detail.get('company') or 'No indicada'}")
        print(f"País: {detail.get('country') or 'No indicado'}")
        print(f"Ubicación: {detail.get('location') or 'No indicada'}")
        print(f"URL: {detail['url']}")
        print(f"Puntaje: {detail['score']}")

        confirm = input(
            "\n¿Deseas generar un CV para esta oferta? (s/n): "
        ).strip().lower()

        if confirm != "s":
            print("Operación cancelada.")
            return

        result = generate_cv(
            job_id=detail["id"],
            job_title=detail["title"],
            description=detail.get("description") or "",
            db=db,
        )

        print("\nCV GENERADO Y REGISTRADO")
        print("=" * 75)
        print(f"Oferta: {detail['title']}")
        print(f"Perfil elegido: {result['profile_key']}")
        print(f"ID de versión: {result['version_id']}")
        print(f"Estado: {result['status']}")
        print(f"Archivo: {result['file_path']}")

        print("\nEl CV queda pendiente de revisión.")

    finally:
        db.close()


if __name__ == "__main__":
    main()
