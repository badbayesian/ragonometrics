FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Poppler provides `pdftotext` and `pdfinfo`
RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install runtime dependencies from pyproject.toml.
RUN pip install --no-cache-dir pip setuptools wheel

# Copy the project into the image
COPY . /app

# Install package (editable keeps dev volume mounts in sync when using docker-compose)
RUN pip install --no-cache-dir -e .

ENV PAPERS_DIR=/app/papers

EXPOSE 8501

# Default command is a no-op; services override this in docker-compose.
CMD ["tail", "-f", "/dev/null"]
