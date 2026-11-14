FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY mcp_server/ mcp_server/

RUN pip install --no-cache-dir .

EXPOSE 8001

CMD ["mcp_server"]
