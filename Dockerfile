# syntax=docker/dockerfile:1
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
COPY assets ./assets
# Опциональный CA secret нужен в окружениях с TLS proxy; в обычном Docker не требуется.
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca SSL_CERT_FILE=/run/secrets/proxy_ca; fi; \
    pip install --no-cache-dir uv==0.13.4 && uv sync --frozen --no-dev --no-editable
COPY alembic.ini ./
COPY alembic ./alembic
COPY scripts ./scripts
RUN chmod -R a+rX /app && useradd --create-home --uid 10001 cardlegends
USER cardlegends
ENV PATH="/app/.venv/bin:$PATH"
CMD ["cardlegends"]
