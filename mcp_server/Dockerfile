# paperRAG-v2 MCP server image
FROM python:3.11-slim

# Sensible container defaults. docker-compose overrides QDRANT_URL etc.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8080 \
    MCP_TRANSPORT=streamable-http \
    QDRANT_URL=http://qdrant:6333

WORKDIR /app

# Install dependencies first so Docker can cache this layer.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Application code.
COPY server.py .

EXPOSE 8080

# Container-level health check hits the /health endpoint served by the audit
# wrapper. `docker ps` / compose will show the container as healthy/unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4)" || exit 1

# Transport, host and port are read from the environment (see config block in
# server.py). Default = streamable-http on 0.0.0.0:8080.
CMD ["python", "server.py"]
