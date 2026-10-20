# 🎯 JobHunt

**JobHunt** es un sistema automatizado diseñado para facilitar y potenciar la búsqueda de empleo para profesionales del sector tecnológico (Desarrollo de Software, Soporte, Sistemas e Informática). 

En lugar de revisar manualmente múltiples portales de empleo, JobHunt rastrea plataformas de trabajo (como LinkedIn, Indeed, etc.), extrae las ofertas relevantes, las evalúa según tu perfil y te permite gestionar todo el ciclo de postulación desde un único Dashboard interactivo.

## ✨ Características Principales

* 🤖 **Búsqueda Automatizada (Scraping):** Extrae ofertas de trabajo de forma automatizada desde múltiples bolsas de empleo y las centraliza en tu base de datos.
* 🎯 **Sistema de Puntuación (Scoring):** Evalúa y clasifica automáticamente cada oferta con un puntaje de "match" basado en qué tanto se ajustan a tu perfil y habilidades.
* 📋 **Gestión Visual (Tablero Kanban):** Organiza tus postulaciones arrastrando las tarjetas por diferentes estados: *Encontradas, Aplicables, CV listo, Postuladas, Entrevista, Oferta* y *Rechazadas*.
* 📊 **Métricas en Tiempo Real:** Visualiza tu rendimiento con tarjetas de KPIs y gráficos de postulaciones semanales.
* 📄 **CVs Adaptados con IA :** Integración nativa con inteligencia artificial para reescribir y adaptar tu currículum automáticamente a los requisitos específicos de cada oferta.

## 🛠️ Tecnologías Utilizadas

* **Backend:** Python 3.11+, FastAPI, Supabase (PostgreSQL), AnyIO.
* **Frontend:** React, Vite, TypeScript, Tailwind CSS, Lucide Icons.

---

## 🚀 Instalación y Configuración

### Requisitos Previos
* **Python** 3.11 o superior.
* **Node.js** (compatible con Vite 8) y **npm**.
* Una base de datos configurada (ej. Supabase) y su cadena de conexión en tu archivo `.env`.

### Pasos de Instalación

Desde la raíz del proyecto, abre tu terminal e instala las dependencias (solo necesitas hacer esto la primera vez):

```powershell
# Instala el entorno de Python y el backend
python -m pip install -e ".[api]"

# Instala las dependencias del frontend (React/Vite)
npm ci --prefix frontend
Luego inicia el API y el frontend, y abre la interfaz en el navegador:

para inicializar 

python -m jobbot serve

```