FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Poppler provides `pdftotext` and `pdfinfo`; libgomp1 is needed for faiss-cpu.
RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install packaging toolchain once.
RUN pip install --no-cache-dir pip setuptools wheel

# Install runtime dependencies in a cacheable layer.
# This step only invalidates when pyproject.toml changes.
COPY pyproject.toml /app/pyproject.toml
RUN python - <<'PY'
import subprocess
import sys
import tomllib
from pathlib import Path

pyproject = tomllib.loads(Path("/app/pyproject.toml").read_text(encoding="utf-8"))
deps = pyproject.get("project", {}).get("dependencies", [])
if deps:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-cache-dir", *deps])
PY

# Copy the project into the image.
COPY . /app

# Install the local package without re-resolving dependencies.
RUN pip install --no-cache-dir --no-deps -e .

ENV PAPERS_DIR=/app/papers

EXPOSE 8501

# Default command is a no-op; services override this in docker-compose.
CMD ["tail", "-f", "/dev/null"]
