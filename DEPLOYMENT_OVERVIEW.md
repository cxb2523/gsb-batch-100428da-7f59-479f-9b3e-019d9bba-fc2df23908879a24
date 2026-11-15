# Deployment Overview: Meta Ads Intelligence Platform

---

## Overview

The platform is a self-contained Python application. It runs as a single process (Uvicorn serving FastAPI) with a background scheduler for sync jobs. It has no external service dependencies beyond a Python runtime and the APIs it connects to (Meta, Google, Telegram).

Three deployment options are supported: direct Python on a Linux VPS, Docker, and development mode.

---

## Infrastructure Components

| Component | Role | Notes |
| --- | --- | --- |
| Python 3.11+ | Application runtime | Required |
| Uvicorn | ASGI server | Runs the FastAPI app |
| APScheduler | Background scheduler | Runs inside the same process |
| SQLite | Main database and tenant databases | No database server needed |
| Nginx or Caddy | Reverse proxy (production) | Handles HTTPS, forwards to port 8000 |
| systemd | Process manager (Linux) | Keeps the app running, restarts on failure |
| Docker (optional) | Container runtime | Alternative to bare-metal Python install |

---

## Production Deployment (Linux VPS)

### Application Layout

```
/opt/meta-ads/
├── run.py
├── requirements.txt
├── .env                        <- Created from .env.example
├── core/
├── db/
├── web/
├── data/                       <- Databases, settings, cache (persistent)
└── logs/                       <- Rotating log files
```

### systemd Service

Create `/etc/systemd/system/meta-ads.service`:

```ini
[Unit]
Description=Meta Ads Intelligence Platform
After=network.target

[Service]
User=www-data
WorkingDirectory=/opt/meta-ads
ExecStart=/opt/meta-ads/venv/bin/python run.py
Restart=on-failure
RestartSec=5
EnvironmentFile=/opt/meta-ads/.env

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable meta-ads
sudo systemctl start meta-ads
```

### Nginx Reverse Proxy

```nginx
server {
    listen 443 ssl;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Use Certbot or Caddy for automatic SSL certificate management.

---

## Docker Deployment

### Dockerfile

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["python", "run.py"]
```

### Run

```bash
docker build -t meta-ads .
docker run -d \
  -p 8000:8000 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  --env-file .env \
  --name meta-ads \
  meta-ads
```

Mount `data/` and `logs/` as volumes so databases, settings, and logs persist across container restarts.

---

## Development Mode

```bash
git clone <repo> meta-ads
cd meta-ads
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your values
python run.py
```

Uvicorn starts with `reload=True` in `run.py`. The app restarts automatically on code changes.

Open `http://localhost:8000`.

---

## Environment Configuration

All secrets and external credentials are loaded from environment variables via `.env` at startup.

| Variable | Required | Description |
| --- | --- | --- |
| `SECRET_KEY` | Yes | Fernet key for encrypting settings files at rest |
| `JWT_SECRET` | Yes | Secret for JWT token signing |
| `SESSION_SECRET` | Yes | Secret for Starlette session cookie signing |
| `ADMIN_USERNAME` | First boot only | Username for the auto-created admin account |
| `ADMIN_PASSWORD` | First boot only | Password for the auto-created admin account |
| `SMTP_HOST` | For email | SMTP server (e.g., `smtp.gmail.com`) |
| `SMTP_PORT` | For email | SMTP port (usually 587) |
| `SMTP_USER` | For email | SMTP login email |
| `SMTP_PASS` | For email | SMTP password or app password |
| `GOOGLE_CLIENT_ID` | Optional | Google OAuth app client ID |
| `GOOGLE_CLIENT_SECRET` | Optional | Google OAuth app client secret |
| `GOOGLE_REDIRECT_URI` | Optional | OAuth callback URL |
| `SHARED_JWT_SECRET` | Optional | Shared secret for license key validation |
| `ADMIN_API_KEY` | Optional | API key for super-admin endpoints |

### Generating the Fernet KEY

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

---

## Persistent Data

These paths must survive restarts. In Docker, mount them as volumes. In systemd, they live in the working directory.

| Path | Contents |
| --- | --- |
| `data/main.db` | Main SQLite database (users, organizations) |
| `data/tenants/` | Per-org SQLite databases, settings, cache, Google API keys |
| `logs/` | Application log files |

---

## Deployment Environments

| Environment | Stack | Purpose |
| --- | --- | --- |
| Development | Python + Uvicorn (reload mode) | Local development, auto-reload on code change |
| Production | systemd + Uvicorn + Nginx | Always-on server with HTTPS and process supervision |
| Docker | Docker + Uvicorn | Portable deployment, easy environment parity |

---

## Upgrade Process

1. Pull the new code.
2. Install any new dependencies: `pip install -r requirements.txt`.
3. Restart the service: `sudo systemctl restart meta-ads`.
4. Database migrations (if any) run automatically on startup via `init_main_db()`.
5. Existing tenant data and settings are preserved.

---

## Logging

The application writes to `logs/error.log` using Python's `RotatingFileHandler`:
- Maximum file size: 500 KB
- Backup files: 3
- Format: `%(asctime)s - %(levelname)s - %(message)s`

All sync events, errors, API failures, and scheduler events are logged here.
