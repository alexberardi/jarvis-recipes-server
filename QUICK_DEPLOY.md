# Quick Deploy Guide

Deploy Jarvis Recipes Server in 4 steps - no git clone needed!

## 📋 Prerequisites

You need a shared PostgreSQL container running. Quick setup:

```bash
# Create shared network
docker network create microservices

# Run postgres
docker run -d \
  --name postgres \
  --network microservices \
  --restart unless-stopped \
  -e POSTGRES_PASSWORD=your-strong-password \
  -v postgres-data:/var/lib/postgresql/data \
  -p 127.0.0.1:5432:5432 \
  postgres:16-alpine

# Create database
docker exec -it postgres psql -U postgres -c "CREATE DATABASE jarvis_recipes;"
docker exec -it postgres psql -U postgres -c "CREATE USER jarvis WITH PASSWORD 'jarvis-password';"
docker exec -it postgres psql -U postgres -c "GRANT ALL PRIVILEGES ON DATABASE jarvis_recipes TO jarvis;"
```

See [POSTGRES_SETUP.md](POSTGRES_SETUP.md) for more details.

---

## 🚀 Deployment Steps

### 1. Download Files
```bash
mkdir -p ~/jarvis-recipes && cd ~/jarvis-recipes
curl -O https://raw.githubusercontent.com/your-username/jarvis-recipes-server/main/docker-compose.staging.yml
curl -o .env https://raw.githubusercontent.com/your-username/jarvis-recipes-server/main/env.staging.template
```

### 2. Configure
```bash
nano .env
```

**Required changes:**
```bash
GITHUB_REPO=your-username/jarvis-recipes-server
DATABASE_URL=postgresql://jarvis:jarvis-password@postgres:5432/jarvis_recipes
AUTH_SECRET_KEY=$(openssl rand -hex 32)
LLM_BASE_URL=http://your-llm-proxy:8000
JARVIS_AUTH_APP_ID=your-app-id
JARVIS_AUTH_APP_KEY=your-app-key
```

### 3. Deploy
```bash
docker-compose -f docker-compose.staging.yml up -d
```

Done! 🎉

---

## 📝 Verify Deployment

```bash
# Check services
docker-compose -f docker-compose.staging.yml ps

# View logs
docker-compose -f docker-compose.staging.yml logs -f

# Test API
curl http://localhost:8001/health
```

---

## 🔄 Update to New Version

```bash
# Change IMAGE_TAG in .env
nano .env  # Set IMAGE_TAG=latest or IMAGE_TAG=1.0.0

# Pull and restart
docker-compose -f docker-compose.staging.yml pull
docker-compose -f docker-compose.staging.yml up -d
```

---

## 🎯 Port Configuration

Change ports in `.env`:

```bash
# Internal container port
APP_PORT=8001

# External host port (what you access)
HOST_PORT=8001
```

Example - run on port 8080:
```bash
HOST_PORT=8080
```

Example - run multiple instances:
```bash
# Instance 1
HOST_PORT=8001

# Instance 2 (separate .env)
HOST_PORT=8002
```

---

## 🗄️ Quick Commands

```bash
# Start
docker-compose -f docker-compose.staging.yml up -d

# Stop
docker-compose -f docker-compose.staging.yml down

# Restart
docker-compose -f docker-compose.staging.yml restart

# Logs
docker-compose -f docker-compose.staging.yml logs -f

# Status
docker-compose -f docker-compose.staging.yml ps

# Update
docker-compose -f docker-compose.staging.yml pull && \
docker-compose -f docker-compose.staging.yml up -d
```

---

## 🔐 First-Time Setup Checklist

- [ ] Download docker-compose.staging.yml
- [ ] Download .env template
- [ ] Set `GITHUB_REPO` in .env
- [ ] Set `POSTGRES_PASSWORD` in .env
- [ ] Generate `AUTH_SECRET_KEY` with `openssl rand -hex 32`
- [ ] Set LLM proxy URL and credentials
- [ ] Configure ports (optional)
- [ ] Run `docker-compose up -d`
- [ ] Test with `curl http://localhost:8001/health`

---

## 🆘 Troubleshooting

**Can't pull image?**
```bash
# Login to GHCR
echo $GITHUB_TOKEN | docker login ghcr.io -u your-username --password-stdin
```

**Port already in use?**
```bash
# Change HOST_PORT in .env
HOST_PORT=8002
```

**Out of memory?**
```bash
# Check resources
docker stats

# Reduce worker memory in docker-compose.staging.yml
```

For more help, see [DEPLOYMENT.md](DEPLOYMENT.md)

