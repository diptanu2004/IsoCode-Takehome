# One image for the whole app: FastAPI serves the API under /api and the built React app on
# every other path. The upload worker is a thread in the same process (deployment.md).

# --- 1. Build the React app ----------------------------------------------------------------
FROM node:22.21-alpine AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- 2. The Python app ---------------------------------------------------------------------
FROM python:3.12-slim AS app
COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    UPLOAD_DIR=/app/var/uploads

WORKDIR /app/backend
# Dependencies first, so a code change doesn't reinstall them.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/ ./
COPY --from=frontend /app/frontend/dist /app/frontend/dist

# Run as an unprivileged user; uploads wait in /app/var until their rows are loaded.
RUN useradd --create-home --uid 10001 settle \
    && mkdir -p /app/var/uploads \
    && chown -R settle /app/var
USER settle

EXPOSE 8000
# Migrations run before serving, so the schema always matches the code. PORT is set by Render.
# --proxy-headers: Render terminates HTTPS, so trust its X-Forwarded-* headers.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
