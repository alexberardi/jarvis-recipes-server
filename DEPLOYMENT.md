# Deployment Guide

## Staging Server Deployment

### Prerequisites
- Docker 20.10+ and Docker Compose installed
- Access to GitHub Container Registry (or make package public)
- Server with at least 8GB RAM (for parse-worker)

---

## 🚀 Quick Start

### 1. Download Compose File
You only need the docker-compose file and an env file - no git clone required!

```bash
# Create a deployment directory
mkdir -p ~/jarvis-recipes-deploy
cd ~/jarvis-recipes-deploy

# Download compose file
wget https://raw.githubusercontent.com/your-username/jarvis-recipes-server/main/docker-compose.staging.yml

# Download env template
wget -O .env https://raw.githubusercontent.com/your-username/jarvis-recipes-server/main/env.staging.template
```

### 2. Configure Environment File

```bash
# Edit the downloaded .env file
nano .env  # or vim, vi, etc.
```

**Required values to set:**
```bash
# Your GitHub repo
GITHUB_REPO=your-username/jarvis-recipes-server

# Strong passwords
POSTGRES_PASSWORD=<generate-strong-password>
AUTH_SECRET_KEY=$(openssl rand -hex 32)

# LLM Proxy
LLM_BASE_URL=http://your-llm-proxy:8000
JARVIS_AUTH_APP_ID=your-app-id
JARVIS_AUTH_APP_KEY=your-app-key
```

### 3. Login to GitHub Container Registry

```bash
# Using personal access token
echo $GITHUB_TOKEN | docker login ghcr.io -u your-username --password-stdin

# Or if package is public, login is not needed
```

**Create a personal access token** (if needed):
1. Go to GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)
2. Generate new token with `read:packages` scope
3. Save it as `GITHUB_TOKEN` environment variable

### 4. Pull and Start Services

```bash
# Pull images
docker-compose -f docker-compose.staging.yml pull

# Start services
docker-compose -f docker-compose.staging.yml up -d

# View logs
docker-compose -f docker-compose.staging.yml logs -f
```

### 5. Verify Deployment

```bash
# Check service health
curl http://localhost:8001/health

# Check API is responding
curl http://localhost:8001/docs

# View logs
docker-compose -f docker-compose.staging.yml logs recipes-api
docker-compose -f docker-compose.staging.yml logs parse-worker
```

---

## 🏷️ Version Management

### Deploy Dev Build (Latest from main)
```bash
IMAGE_TAG=dev docker-compose -f docker-compose.staging.yml --env-file .env.staging up -d
```

### Deploy Production Build (Latest release)
```bash
IMAGE_TAG=latest docker-compose -f docker-compose.staging.yml --env-file .env.staging up -d
```

### Deploy Specific Version
```bash
IMAGE_TAG=1.0.0 docker-compose -f docker-compose.staging.yml --env-file .env.staging up -d
```

### Check Current Version
```bash
docker-compose -f docker-compose.staging.yml ps
```

---

## 🔄 Updates and Rollbacks

### Update to Latest Dev Build

```bash
# Set to dev tag in .env.staging
IMAGE_TAG=dev

# Pull new image
docker-compose -f docker-compose.staging.yml --env-file .env.staging pull

# Restart services
docker-compose -f docker-compose.staging.yml --env-file .env.staging up -d
```

### Rollback to Previous Version

```bash
# Change IMAGE_TAG to previous version in .env.staging
IMAGE_TAG=1.0.0

# Pull and restart
docker-compose -f docker-compose.staging.yml --env-file .env.staging pull
docker-compose -f docker-compose.staging.yml --env-file .env.staging up -d
```

---

## 🗄️ Database Management

### Backup Database

```bash
docker-compose -f docker-compose.staging.yml exec postgres pg_dump \
  -U jarvis jarvis_recipes > backup_$(date +%Y%m%d_%H%M%S).sql
```

### Restore Database

```bash
docker-compose -f docker-compose.staging.yml exec -T postgres psql \
  -U jarvis jarvis_recipes < backup_20240101_120000.sql
```

### Connect to Database

```bash
docker-compose -f docker-compose.staging.yml exec postgres psql \
  -U jarvis -d jarvis_recipes
```

---

## 🔍 Troubleshooting

### View Logs

```bash
# All services
docker-compose -f docker-compose.staging.yml logs -f

# Specific service
docker-compose -f docker-compose.staging.yml logs -f recipes-api
docker-compose -f docker-compose.staging.yml logs -f parse-worker
docker-compose -f docker-compose.staging.yml logs -f postgres

# Last 100 lines
docker-compose -f docker-compose.staging.yml logs --tail=100
```

### Restart Services

```bash
# Restart all
docker-compose -f docker-compose.staging.yml restart

# Restart specific service
docker-compose -f docker-compose.staging.yml restart recipes-api
docker-compose -f docker-compose.staging.yml restart parse-worker
```

### Check Service Status

```bash
docker-compose -f docker-compose.staging.yml ps
```

### Access Container Shell

```bash
# API container
docker-compose -f docker-compose.staging.yml exec recipes-api bash

# Worker container
docker-compose -f docker-compose.staging.yml exec parse-worker bash
```

### OCR Service

OCR processing is now handled by the separate `jarvis-ocr-service` microservice.
Ensure `JARVIS_OCR_SERVICE_URL` is configured in your environment.

---

## 🔐 Security Best Practices

### 1. Use Strong Passwords
```bash
# Generate strong password
openssl rand -base64 32

# Generate AUTH_SECRET_KEY
openssl rand -hex 32
```

### 2. Limit Database Access
The postgres port is bound to `127.0.0.1:5432` (localhost only) by default.

To connect from outside the server, use SSH tunnel:
```bash
ssh -L 5432:localhost:5432 user@your-server
```

### 3. Use HTTPS in Production
Add a reverse proxy (nginx or traefik) with SSL certificates.

### 4. Firewall Configuration
```bash
# Allow API port
sudo ufw allow 8001/tcp

# Or restrict to specific IP
sudo ufw allow from YOUR_IP to any port 8001
```

---

## 📊 Monitoring

### Resource Usage

```bash
# Real-time stats
docker stats

# Specific services
docker stats jarvis-recipes-server-recipes-api-1 jarvis-recipes-server-parse-worker-1
```

### Disk Usage

```bash
# Check volume sizes
docker system df -v

# Check specific volumes
du -sh $(docker volume inspect jarvis-recipes-server_postgres-data --format '{{.Mountpoint}}')
du -sh $(docker volume inspect jarvis-recipes-server_media-files --format '{{.Mountpoint}}')
```

---

## 🧹 Maintenance

### Clean Up Old Images

```bash
# Remove unused images
docker image prune -a

# Remove specific old version
docker rmi ghcr.io/your-username/jarvis-recipes-server:1.0.0
```

### Clean Up Logs (if growing too large)

```bash
# Truncate logs
docker-compose -f docker-compose.staging.yml logs --no-log-prefix > /dev/null

# Or configure log rotation in /etc/docker/daemon.json:
# {
#   "log-driver": "json-file",
#   "log-opts": {
#     "max-size": "10m",
#     "max-file": "3"
#   }
# }
```

---

## 🔧 Advanced Configuration

### Using External Database

If you have a managed PostgreSQL instance:

1. Remove the `postgres` service from docker-compose.staging.yml
2. Update DATABASE_URL in .env.staging:
   ```bash
   DATABASE_URL=postgresql://user:pass@external-db.example.com:5432/dbname
   ```

### Custom API Port

```bash
# In .env
HOST_PORT=8080  # Change external port

# Or change both internal and external
APP_PORT=8080
HOST_PORT=8080

# Then access at http://your-server:8080
```

### Scale Workers

```bash
# Run multiple worker instances
docker-compose -f docker-compose.staging.yml up -d --scale parse-worker=3
```

---

## 📝 Environment Variables Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GITHUB_REPO` | Yes | - | Your GitHub repo (user/repo) |
| `IMAGE_TAG` | No | `dev` | Image tag to deploy |
| `POSTGRES_PASSWORD` | Yes | - | Database password |
| `AUTH_SECRET_KEY` | Yes | - | JWT signing key |
| `LLM_BASE_URL` | Yes | - | LLM proxy URL |
| `JARVIS_AUTH_APP_ID` | Yes | - | Jarvis authentication app ID (for LLM proxy and OCR service) |
| `JARVIS_AUTH_APP_KEY` | Yes | - | Jarvis authentication app key (for LLM proxy and OCR service) |
| `APP_PORT` | No | `8001` | Internal container port |
| `HOST_PORT` | No | `8001` | External port exposed to host |
| `POSTGRES_DB` | No | `jarvis_recipes` | Database name |
| `POSTGRES_USER` | No | `jarvis` | Database user |

---

## 🆘 Getting Help

- **Logs not helpful?** Enable debug mode: Add `LOG_LEVEL=DEBUG` to .env.staging
- **Out of memory?** Reduce worker memory limit or upgrade server
- **Can't pull images?** Check GitHub token permissions or make package public

---

## 📚 Related Documentation

- [Docker Workflow](.github/DOCKER_WORKFLOW.md) - CI/CD and image tagging
- [Docker Build Troubleshooting](.github/DOCKER_BUILD_TROUBLESHOOTING.md) - Build issues
- [README.md](README.md) - Development setup

