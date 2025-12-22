FROM python:3.11-slim

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml .
COPY src/ src/

# Install package
RUN pip install --no-cache-dir -e .

# Download embedding model at build time (optional, can be slow)
# RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# Create data directory
RUN mkdir -p /data

# Default command
CMD ["python", "-m", "src.cli", "run", "-c", "/config/config.yaml"]
