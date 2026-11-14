FROM python:3.12-slim

WORKDIR /app

# Install system deps for Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir .
RUN playwright install chromium
RUN playwright install-deps chromium

COPY server.py .

EXPOSE 3100
CMD ["python", "server.py"]
