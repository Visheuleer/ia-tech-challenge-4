FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /uvx /bin/
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --all-extras --no-dev
COPY migrations ./migrations
COPY alembic.ini ./
COPY .streamlit ./.streamlit
ENV PATH="/app/.venv/bin:$PATH"
CMD ["uvicorn", "clinical_monitor.api.app:create_app", "--factory", "--host", "0.0.0.0"]
