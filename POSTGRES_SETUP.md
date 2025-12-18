# Shared PostgreSQL Setup

This guide shows how to set up a single PostgreSQL container that multiple microservices can connect to.

## 🗄️ Quick Setup

### 1. Create a Shared Network

```bash
docker network create microservices
```

### 2. Run PostgreSQL Container

```bash
docker run -d \
  --name postgres \
  --network microservices \
  --restart unless-stopped \
  -e POSTGRES_PASSWORD=your-strong-password \
  -v postgres-data:/var/lib/postgresql/data \
  -p 127.0.0.1:5432:5432 \
  postgres:16-alpine
```

### 3. Create Database for Jarvis Recipes

```bash
docker exec -it postgres psql -U postgres -c "CREATE DATABASE jarvis_recipes;"
docker exec -it postgres psql -U postgres -c "CREATE USER jarvis WITH PASSWORD 'jarvis-password';"
docker exec -it postgres psql -U postgres -c "GRANT ALL PRIVILEGES ON DATABASE jarvis_recipes TO jarvis;"
```

### 4. Update docker-compose.staging.yml

Add the network to your services:

```yaml
services:
  recipes-api:
    # ... existing config
    networks:
      - microservices

  parse-worker:
    # ... existing config
    networks:
      - microservices

networks:
  microservices:
    external: true
```

### 5. Configure .env

```bash
# Use 'postgres' as hostname (container name)
DATABASE_URL=postgresql://jarvis:jarvis-password@postgres:5432/jarvis_recipes
```

---

## 🔐 Security Best Practices

### Use Strong Passwords

```bash
# Generate strong password
POSTGRES_PASSWORD=$(openssl rand -base64 32)
echo "POSTGRES_PASSWORD=$POSTGRES_PASSWORD"

# Use in docker run command
docker run -d \
  --name postgres \
  --network microservices \
  --restart unless-stopped \
  -e POSTGRES_PASSWORD=$POSTGRES_PASSWORD \
  -v postgres-data:/var/lib/postgresql/data \
  -p 127.0.0.1:5432:5432 \
  postgres:16-alpine
```

### Bind to Localhost Only

Note the `-p 127.0.0.1:5432:5432` - this prevents external access.

To connect from your local machine for debugging:

```bash
# SSH tunnel
ssh -L 5432:localhost:5432 user@your-server

# Then connect locally
psql postgresql://jarvis:password@localhost:5432/jarvis_recipes
```

---

## 📊 Multiple Services Example

### Service 1: Jarvis Recipes
```bash
# In jarvis-recipes/.env
DATABASE_URL=postgresql://jarvis:password@postgres:5432/jarvis_recipes
```

### Service 2: Another Microservice
```bash
# In other-service/.env
DATABASE_URL=postgresql://other_user:password@postgres:5432/other_service
```

Both connect to the same postgres container, different databases.

---

## 🔧 Management Commands

### View All Databases

```bash
docker exec -it postgres psql -U postgres -c "\l"
```

### Create New Database for Another Service

```bash
docker exec -it postgres psql -U postgres -c "CREATE DATABASE service_name;"
docker exec -it postgres psql -U postgres -c "CREATE USER service_user WITH PASSWORD 'password';"
docker exec -it postgres psql -U postgres -c "GRANT ALL PRIVILEGES ON DATABASE service_name TO service_user;"
```

### Connect to Database

```bash
docker exec -it postgres psql -U jarvis -d jarvis_recipes
```

### Backup Database

```bash
docker exec postgres pg_dump -U jarvis jarvis_recipes > backup_$(date +%Y%m%d).sql
```

### Restore Database

```bash
cat backup_20240101.sql | docker exec -i postgres psql -U jarvis -d jarvis_recipes
```

---

## 🗄️ Persistence

The postgres container uses a named volume `postgres-data` that persists across container restarts:

```bash
# View volume
docker volume inspect postgres-data

# Backup volume
docker run --rm -v postgres-data:/data -v $(pwd):/backup \
  alpine tar czf /backup/postgres-backup.tar.gz /data

# Restore volume
docker run --rm -v postgres-data:/data -v $(pwd):/backup \
  alpine tar xzf /backup/postgres-backup.tar.gz -C /
```

---

## 🔄 Upgrading PostgreSQL

When upgrading Postgres versions:

```bash
# 1. Backup data
docker exec postgres pg_dumpall -U postgres > backup_all.sql

# 2. Stop and remove old container
docker stop postgres
docker rm postgres

# 3. Start new version
docker run -d \
  --name postgres \
  --network microservices \
  --restart unless-stopped \
  -e POSTGRES_PASSWORD=your-password \
  -v postgres-data-new:/var/lib/postgresql/data \
  -p 127.0.0.1:5432:5432 \
  postgres:17-alpine

# 4. Restore data
cat backup_all.sql | docker exec -i postgres psql -U postgres
```

---

## 🆘 Troubleshooting

### Can't Connect to Database

```bash
# Check postgres is running
docker ps | grep postgres

# Check network
docker network inspect microservices

# Check if service is on the network
docker inspect recipes-api | grep -A 10 Networks

# Test connection from service container
docker exec recipes-api psql postgresql://jarvis:password@postgres:5432/jarvis_recipes -c "SELECT 1;"
```

### Connection Refused

Make sure both containers are on the same network:

```bash
docker network connect microservices recipes-api
docker network connect microservices postgres
```

### Too Many Connections

Increase max connections in postgres:

```bash
docker run -d \
  --name postgres \
  --network microservices \
  -e POSTGRES_PASSWORD=password \
  -v postgres-data:/var/lib/postgresql/data \
  postgres:16-alpine \
  -c max_connections=200
```

---

## 📝 Alternative: Docker Compose for Postgres

If you prefer, create a separate `docker-compose.postgres.yml`:

```yaml
version: "3.9"

services:
  postgres:
    image: postgres:16-alpine
    container_name: postgres
    restart: unless-stopped
    environment:
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?required}
    volumes:
      - postgres-data:/var/lib/postgresql/data
    ports:
      - "127.0.0.1:5432:5432"
    networks:
      - microservices
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  postgres-data:

networks:
  microservices:
    name: microservices
```

Start it:
```bash
docker-compose -f docker-compose.postgres.yml up -d
```

Then your microservices connect to it via the `microservices` network.

