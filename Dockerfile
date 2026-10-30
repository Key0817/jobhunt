FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APP_ENV=production PORT=8000
RUN apt-get update && apt-get install -y --no-install-recommends libreoffice-writer fonts-liberation fonts-crosextra-carlito fonts-dejavu-core ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir -e '.[api]' && useradd --create-home --uid 10001 jobhunt
COPY config/search.yml config/companies.yml config/ranking.yml ./config/
RUN mkdir -p /app/generated && chown -R jobhunt:jobhunt /app
USER jobhunt
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/healthz',timeout=4)"
CMD ["sh", "-c", "exec uvicorn jobbot.api:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers"]
