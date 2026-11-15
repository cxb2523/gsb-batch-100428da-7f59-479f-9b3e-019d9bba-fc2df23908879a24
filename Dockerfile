FROM python:3.13-slim

LABEL maintainer="ExploiterX Contributors"
LABEL description="Professional Web Vulnerability Scanner"
LABEL version="2.0.2"

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create directories for output
RUN mkdir -p /app/reports /app/logs

# Set permissions
RUN chmod +x exploiterX.py

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV LOG_LEVEL=INFO

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

# Default command
ENTRYPOINT ["python", "exploiterX.py"]
CMD []
