# syntax=docker/dockerfile:1
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
COPY assets ./assets
# Use standard dependency installation for Railway and other builders.
RUN pip install --no-cache-dir uv==0.13.4 && uv sync --frozen --no-dev --no-editable
COPY alembic.ini ./
COPY alembic ./alembic
COPY scripts ./scripts
RUN chmod -R a+rX /app && useradd --create-home --uid 10001 cardlegends
USER cardlegends
ENV PATH="/app/.venv/bin:$PATH"
CMD ["cardlegends"]
