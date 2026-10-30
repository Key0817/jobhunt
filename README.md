# 🎯 JobHunt

**JobHunt** es una aplicación para automatizar la recolección de ofertas de empleo tecnológico y gestionar candidaturas desde un único lugar. Está orientada a perfiles de desarrollo de software, soporte técnico, sistemas e informática, con filtros de elegibilidad para Costa Rica y oportunidades remotas compatibles.

El sistema reúne ofertas de plataformas de reclutamiento, páginas públicas de empresas y alertas recibidas por Gmail. Normaliza los datos, evita duplicados, calcula una puntuación explicable y permite consultar la oferta original, organizar su seguimiento y preparar un CV personalizado y una carta de presentación.

La aplicación puede ejecutarse localmente para desarrollo o desplegarse con el frontend en Vercel, el backend en Render y los datos y documentos privados en Supabase.

## ✨ Características principales

- 🤖 **Recolección desde múltiples fuentes:** conectores para Get on Board, Greenhouse, SmartRecruiters, Lever, Ashby, Workday y otras plataformas, además de lectores compartidos para páginas públicas. LinkedIn e Indeed se integran mediante alertas por correo, sin automatizar sesiones en esos portales.
- 📬 **Alertas de Gmail con OAuth:** importa las ofertas reconocidas dentro de cada correo de una etiqueta dedicada. Consulta mensajes no leídos, conserva el historial de procesamiento y no modifica su estado de lectura.
- 🎯 **Ranking explicable:** puntúa cada oferta de 0 a 100 mediante reglas locales sobre puesto, habilidades, experiencia, inglés y antigüedad. Muestra las razones del resultado y permite ajustar las preferencias.
- 📋 **Seguimiento de candidaturas:** tablero por estados Encontradas, Aplicables, CV listo, Postuladas, Entrevista, Oferta, Rechazadas y Descartadas. Los cambios se realizan con selectores y quedan registrados en el historial.
- 🔎 **Filtros y paginación:** búsqueda visual por fuente, estado, modalidad, puntuación y fecha de incorporación, con páginas independientes en el tablero y 50 ofertas por página en Ofertas.
- ⚡ **Actualizaciones inmediatas:** refleja los cambios de estado sin recargar toda la vista, conserva páginas y filtros al navegar y revierte el cambio si falla el guardado.
- 📊 **Métricas de candidaturas:** muestra el total de ofertas, incorporaciones recientes, postulaciones, entrevistas, ofertas recibidas y evolución semanal.
- 📄 **CV personalizado con IA:** utiliza los hechos reales del perfil profesional para generar únicamente el párrafo `{{ perfil }}` de una plantilla DOCX, conservando su contenido fijo y formato.
- ✉️ **Cartas de presentación:** genera y guarda una cover letter vinculada a la oferta y a la versión del CV, con opciones para copiarla o regenerarla.
- 🗂️ **Versiones y documentos privados:** biblioteca de CV con estados de revisión, descarga PDF mediante LibreOffice y almacenamiento local o en un bucket privado de Supabase.
- 🔐 **Acceso protegido:** integración con Supabase Auth y verificación del propietario en el backend; el desarrollo local también admite un modo sin login.
- 🩺 **Salud de las fuentes:** registra corridas completas, parciales y fallidas, motivos de descarte, duplicados y páginas pendientes. Incluye reintentos acotados y reanudación de los lectores que admiten checkpoints.
- 🔄 **Recolección programada:** GitHub Actions ejecuta las fuentes por grupos y conserva sus resultados en la base de datos compartida.

JobHunt no envía candidaturas automáticamente. El usuario revisa el CV y la carta y postula desde el enlace original. Las actualizaciones inmediatas de la interfaz no implican sincronización entre dispositivos mediante websockets.

## 🛠️ Tecnologías utilizadas

| Componente | Tecnologías |
| --- | --- |
| Backend y CLI | Python 3.11+, FastAPI, Uvicorn y Pydantic. |
| Base de datos | PostgreSQL/Supabase con psycopg; SQLite para desarrollo y pruebas. |
| Frontend | React, TypeScript, Vite, Tailwind CSS y Lucide Icons. |
| Autenticación y archivos | Supabase Auth y Supabase Storage privado. |
| Recolección | APIs públicas, Beautiful Soup, lectores compartidos y Playwright/Chromium cuando se necesita JavaScript. |
| Gmail | Gmail API, OAuth con PKCE y cifrado Fernet de credenciales. |
| IA y documentos | Gemini u Ollama, docxtpl y LibreOffice. |
| Pruebas y automatización | unittest, pruebas de Node, comprobaciones en Chromium y GitHub Actions. |
| Despliegue | Docker y Render para la API; Vercel para el frontend. |

## 🚀 Instalación y configuración para desarrollo

### Requisitos previos

- **Python 3.11 o superior**. El contenedor y el workflow utilizan Python 3.12.
- **Node.js 24.x y npm** para el frontend y sus pruebas.
- **SQLite o PostgreSQL/Supabase**, según el entorno elegido.
- **LibreOffice** para exportar a PDF.
- Un perfil y una plantilla privados, además de Gemini u Ollama configurado, para generar CV y cartas. No son necesarios para consultar ofertas o ejecutar el ranking.

### 1. Instalar dependencias

Desde la raíz del repositorio, crea un entorno virtual si todavía no existe:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[api]"
npm ci --prefix frontend
```

Para las fuentes que necesitan un navegador, instala también:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[browser]"
.\.venv\Scripts\python.exe -m playwright install chromium
```

### 2. Configurar las variables locales

Copia los ejemplos únicamente si aún no tienes esos archivos:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
if (-not (Test-Path frontend/.env.local)) { Copy-Item frontend/.env.example frontend/.env.local }
```

El archivo **`.env` de la raíz** corresponde al backend y la CLI. Para una base local independiente puedes configurar:

```dotenv
APP_ENV=development
AUTH_REQUIRED=false
DATABASE_URL=sqlite:///jobbot.db
DOCUMENT_STORAGE=local
FRONTEND_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

Si utilizas Supabase, conserva su conexión PostgreSQL en `DATABASE_URL`. Para usar archivos de perfil y plantilla locales, deja vacíos `PROFILE_OBJECT_KEY` y `CV_TEMPLATE_OBJECT_KEY`.

El archivo **`frontend/.env.local`** corresponde al frontend:

```dotenv
VITE_API_URL=http://localhost:8000
VITE_SUPABASE_URL=
VITE_SUPABASE_ANON_KEY=
```

Dejar los dos valores de Supabase vacíos permite trabajar localmente sin login. Para probar autenticación, configura esos valores públicos y, en el backend, `AUTH_REQUIRED=true`, `SUPABASE_URL`, `SUPABASE_ANON_KEY` y `APP_OWNER_ID`.

No copies claves privadas a variables `VITE_*`. Mantén el desarrollo sin autenticación escuchando únicamente en loopback y reinicia los procesos cuando cambies su configuración.

### 3. Inicializar la base de datos y ejecutar

```powershell
.\.venv\Scripts\python.exe -m jobbot init-db
.\.venv\Scripts\python.exe -m jobbot serve
```

`serve` inicia la API en **http://127.0.0.1:8000**, el frontend en **http://127.0.0.1:5173** y abre el navegador. Usa `Ctrl+C` para detenerlos. La base estará vacía hasta ejecutar una recolección:

```powershell
.\.venv\Scripts\python.exe -m jobbot collect --sources getonboard
```

Para trabajar con recarga automática del backend, ejecuta en dos terminales desde la raíz:

```powershell
# Terminal 1: API con recarga de Python
.\.venv\Scripts\python.exe -m uvicorn jobbot.api:app --reload --host 127.0.0.1 --port 8000
```

```powershell
# Terminal 2: frontend con HMR
npm --prefix frontend run dev -- --host 127.0.0.1
```

