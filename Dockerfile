FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Poppler provides `pdftotext` and `pdfinfo`
RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install a minimal set of runtime dependencies. We rely on pyproject.toml for packaging,
# but installing the core deps directly keeps the image simple and fast.
RUN pip install --no-cache-dir pip setuptools wheel
RUN pip install --no-cache-dir openai streamlit requests

# Copy the project into the image
COPY . /app

ENV PAPERS_DIR=/app/papers

EXPOSE 8501

# Default command is a no-op; services override this in docker-compose.
CMD ["tail", "-f", "/dev/null"]
