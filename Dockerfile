# One image for both services: the FastAPI agents service (default) and the Streamlit app.
# The local model stays on the host (Ollama), reached through host.docker.internal.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first, so code changes do not reinstall every package
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY src ./src
COPY mcp_server ./mcp_server
COPY scripts ./scripts
COPY eval ./eval
COPY tests ./tests
COPY app.py pytest.ini ./

# Build the SIMULATED data inside the image. The fixed seeds give exactly the same files as on the laptop.
RUN python scripts/generate_data.py > /dev/null && python scripts/generate_data.py --dev > /dev/null

# Run as a normal user, not root. /app/state holds the audit log and paused runs (a Docker volume).
RUN useradd --create-home appuser \
    && mkdir -p /app/state /app/data/uploads \
    && chown -R appuser /app/state /app/data
USER appuser

ENV OLLAMA_BASE_URL=http://host.docker.internal:11434 \
    CHECKPOINT_DB=/app/state/checkpoints.db \
    AUDIT_DB=/app/state/audit.db

EXPOSE 8000 8501
CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]
