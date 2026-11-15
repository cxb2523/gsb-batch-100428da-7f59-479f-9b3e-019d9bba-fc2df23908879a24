# Docker Deployment Guide for ExploiterX

This guide provides instructions for deploying ExploiterX using Docker and Docker Compose.

## 📋 Prerequisites

- Docker 20.10+
- Docker Compose 1.29+ (for multi-container setup)
- 2GB+ available disk space
- 2 CPU cores recommended

## 🚀 Quick Start

### Option 1: Docker Compose (Recommended)

The fastest way to get started with ExploiterX and a vulnerable test environment:

```bash
# Clone the repository
git clone https://github.com/anishalx/ExploiterX.git
cd ExploiterX

# Start all services (ExploiterX, DVWA, Juice Shop)
docker-compose up -d

# View running containers
docker-compose ps

# Check DVWA is ready
curl http://localhost:8888

# Access the ExploiterX container
docker-compose exec exploiterx bash
```

### Option 2: Standalone Docker Image

If you only want to run ExploiterX without vulnerable apps:

```bash
# Build the image
docker build -t exploiterx:latest .

# Run a scan with interactive mode
docker run -it \
  -v $(pwd)/reports:/app/reports \
  -v $(pwd)/logs:/app/logs \
  exploiterx:latest

# Run with automatic target
docker run -it \
  -v $(pwd)/reports:/app/reports \
  exploiterx:latest --target http://target.com
```

## 🏗️ Architecture

The Docker Compose setup includes:

1. **ExploiterX Scanner**: Main vulnerability scanning tool
2. **DVWA (Port 8888)**: Deliberately vulnerable web application for testing
3. **OWASP Juice Shop (Port 3000)**: Modern vulnerable web application
4. **Shared Network**: All services communicate via `exploiter-network`

## 📊 Configuration

### Environment Variables

Set environment variables for the ExploiterX service:

```yaml
environment:
  - PYTHONUNBUFFERED=1
  - LOG_LEVEL=DEBUG  # Set to INFO, WARNING, ERROR, or DEBUG
  - TIMEOUT=15       # Request timeout in seconds
  - THREADS=5        # Number of concurrent threads
```

### Volume Mounts

```yaml
volumes:
  - ./reports:/app/reports    # Scan reports
  - ./logs:/app/logs          # Application logs
  - ./config:/app/config:ro   # Configuration files (read-only)
```

### Resource Limits

Adjust Docker resource allocation:

```yaml
deploy:
  resources:
    limits:
      cpus: '2'       # Max 2 CPU cores
      memory: 1G      # Max 1GB RAM
```

## 🔍 Usage Examples

### Scan DVWA with Docker Compose

```bash
# Start services
docker-compose up -d

# Access the scanner
docker-compose exec exploiterx bash

# Inside the container:
python exploiterX.py

# When prompted:
# Target URL: http://dvwa:80/DVWA
# Ignore links: /logout, /admin
# Authentication: admin / password
```

### Scan Juice Shop

```bash
# Access the scanner
docker-compose exec exploiterx bash

# Inside the container:
python exploiterX.py

# When prompted:
# Target URL: http://juice-shop:3000
```

### Mount Custom Configuration

```bash
# Create a custom config
cp config/config.yaml my-config.yaml

# Run with custom config
docker run -it \
  -v $(pwd)/my-config.yaml:/app/config/config.yaml:ro \
  -v $(pwd)/reports:/app/reports \
  exploiterx:latest
```

### Generate HTML Report

```bash
docker-compose exec exploiterx bash

# Inside the container:
python exploiterX.py

# Reports are saved to ./reports/scan_YYYY-MM-DD_HHMMSS.*
```

## 🔧 Docker Compose Commands

### Lifecycle Management

```bash
# Start services
docker-compose up

# Start in background
docker-compose up -d

# Stop services
docker-compose stop

# Stop and remove containers
docker-compose down

# Remove all data including volumes
docker-compose down -v

# View logs
docker-compose logs -f exploiterx

# View specific service logs
docker-compose logs -f dvwa
```

### Service Management

```bash
# Rebuild images
docker-compose build

# Restart a service
docker-compose restart exploiterx

# Execute command in container
docker-compose exec exploiterx python exploiterX.py

# Access shell
docker-compose exec exploiterx bash
```

## 📈 Performance Tuning

### Optimize for Large Scans

```yaml
environment:
  - THREADS=10        # Increase concurrent threads
  - TIMEOUT=20        # Increase timeout for slow targets
deploy:
  resources:
    limits:
      cpus: '4'       # Allocate more CPUs
      memory: 2G      # Allocate more RAM
```

### Optimize for Limited Resources

```yaml
environment:
  - THREADS=2         # Reduce concurrent threads
deploy:
  resources:
    limits:
      cpus: '1'
      memory: 512M
```

## 🔐 Security Considerations

### Never Expose to Internet

```bash
# BAD: Exposes DVWA to internet
ports:
  - "0.0.0.0:8888:80"

# GOOD: Local access only
ports:
  - "127.0.0.1:8888:80"
```

### Use Read-Only Config

```yaml
volumes:
  - ./config:/app/config:ro  # Read-only config
```

### Isolate Networks

The default setup uses a private Docker network. Container access is isolated by default.

## 🐛 Troubleshooting

### Container won't start

```bash
# Check logs
docker-compose logs exploiterx

# Rebuild the image
docker-compose build --no-cache exploiterx

# Restart
docker-compose restart exploiterx
```

### Can't reach DVWA from ExploiterX

```bash
# Check network
docker network ls
docker network inspect exploiter_network

# Check DVWA is running
docker-compose ps dvwa
curl http://localhost:8888
```

### Permission denied errors

```bash
# Fix permissions
sudo chown -R $USER:$USER ./reports ./logs

# Or run as sudo
sudo docker-compose up -d
```

### Out of memory

```bash
# Reduce threads and timeouts
# Edit docker-compose.yml:
environment:
  - THREADS=2
  - TIMEOUT=10

# Restart
docker-compose down
docker-compose up -d
```

## 📦 Image Information

### Image Size

- Base image: `python:3.13-slim` (180MB)
- With dependencies: ~400MB
- Full image with reports: ~500MB

### Layer Details

```dockerfile
FROM python:3.13-slim              # 180MB
RUN apt-get install...             # System deps
COPY requirements.txt              # Dependencies
RUN pip install -r requirements    # Python packages
COPY . .                           # Application code
```

## 🔄 CI/CD Integration

### GitHub Actions

```yaml
- name: Run ExploiterX in Docker
  run: |
    docker-compose up -d
    docker-compose exec -T exploiterx python exploiterX.py
    docker-compose down
```

### GitLab CI

```yaml
test:
  script:
    - docker-compose up -d
    - docker-compose exec -T exploiterx pytest tests/
    - docker-compose down
```

## 📚 Additional Resources

- [Docker Documentation](https://docs.docker.com/)
- [Docker Compose Documentation](https://docs.docker.com/compose/)
- [DVWA GitHub](https://github.com/digininja/DVWA)
- [OWASP Juice Shop](https://owasp.org/www-project-juice-shop/)

## ❓ FAQ

**Q: Can I use ExploiterX Docker image without Docker Compose?**
A: Yes! See Option 2 in Quick Start section.

**Q: How do I persist reports between container restarts?**
A: Use volume mounts: `-v $(pwd)/reports:/app/reports`

**Q: Can I scan external targets?**
A: Yes, just provide the target URL when prompted. Make sure the target is accessible from the container.

**Q: How do I update ExploiterX in Docker?**
A: Pull the latest code and rebuild:
```bash
git pull
docker-compose build --no-cache
docker-compose up -d
```

---

**Made with ❤️ for the security community**
