# Hosted control plane + trusted web only. No desktop reader or model is started.
FROM node:22-bookworm-slim AS web
WORKDIR /app
RUN npm install --global pnpm@11.10.0
COPY . .
RUN pnpm install --frozen-lockfile --ignore-scripts \
    && pnpm --filter @accessforge/web build

FROM ghcr.io/astral-sh/uv:0.11.28 AS uv
FROM python:3.13-slim-bookworm AS python-build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY . .
RUN uv sync --frozen --no-dev --no-editable
RUN .venv/bin/python -c "from accessforge_api.app import create_app; from accessforge_contracts import available_schemas, load_schema; from accessforge_persistence import expected_migrations; [load_schema(s) for s in available_schemas()]; assert len(expected_migrations()) >= 72"

FROM python:3.13-slim-bookworm AS runtime
WORKDIR /app
COPY --from=python-build /app /app
COPY --from=web /app/apps/web/dist /app/web
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ACCESSFORGE_HOST=0.0.0.0 \
    ACCESSFORGE_PORT=8080 \
    ACCESSFORGE_ENVIRONMENT=production \
    ACCESSFORGE_IDENTITY_PROVIDER=none \
    ACCESSFORGE_WEB_DIST_DIRECTORY=/app/web
USER 10001:10001
EXPOSE 8080
CMD ["python", "-m", "accessforge_api"]
