# syntax=docker/dockerfile:1
# Image de l'appli « Analyste » (Django + gunicorn). Build multi-étapes comme
# Recrutement : la compilation de mysqlclient reste dans l'étage « builder ».
# Build depuis analyste/ :  docker compose build
# ⚠️ Seul webapp/ est copié : jamais de .env, de cache de données ni de vidéo
# dans l'image (cf. .dockerignore).

FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        default-libmysqlclient-dev \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY webapp/requirements-web.txt /requirements-web.txt
RUN pip install --prefix=/install -r /requirements-web.txt

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        libmariadb3 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

WORKDIR /app
COPY webapp/ ./webapp/

COPY webapp/entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

# Compte non privilégié : gunicorn ne tourne jamais en root.
RUN groupadd --gid 10002 analyste \
    && useradd --uid 10002 --gid 10002 --no-create-home --shell /usr/sbin/nologin analyste \
    && mkdir -p /app/webapp/staticfiles \
    && chown -R analyste:analyste /app

USER analyste
WORKDIR /app/webapp

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
CMD ["gunicorn", "analyste_site.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "2", \
     "--timeout", "120", \
     "--worker-tmp-dir", "/dev/shm"]
