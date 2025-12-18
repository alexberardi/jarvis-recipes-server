# Docker Build & Push Workflow

This repository uses GitHub Actions to automatically build and push Docker images to GitHub Container Registry (GHCR).

## 📦 Image Location

```
ghcr.io/<your-username>/jarvis-recipes-server
```

## 🏷️ Tagging Strategy

### Dev Builds (Push to `main`)
Every push to the `main` branch builds and tags the image as `dev`:

```bash
docker pull ghcr.io/<your-username>/jarvis-recipes-server:dev
```

**Use case:** Testing WIP changes before creating a release.

### Production Builds (Git Tags)
Creating a git tag like `v1.0.0` builds and tags the image as:
- `1.0.0` (exact version)
- `1.0` (major.minor)
- `latest` (production release)

```bash
# Pull latest production
docker pull ghcr.io/<your-username>/jarvis-recipes-server:latest

# Pull specific version
docker pull ghcr.io/<your-username>/jarvis-recipes-server:1.0.0
```

**Use case:** Stable releases for production deployment.

## 🏗️ Architecture Support

The workflow builds multi-platform images for:
- `linux/amd64` - Common servers, Intel/AMD processors
- `linux/arm64` - Apple Silicon Macs (M1/M2/M3), AWS Graviton

Docker automatically pulls the correct architecture for your system.

## 🚀 Usage Examples

### Deploying Dev Build

```bash
# Pull dev image
docker pull ghcr.io/<your-username>/jarvis-recipes-server:dev

# Update docker-compose.yml
services:
  recipes-api:
    image: ghcr.io/<your-username>/jarvis-recipes-server:dev
    # ... rest of config
```

### Deploying Production Build

```bash
# Pull latest production
docker pull ghcr.io/<your-username>/jarvis-recipes-server:latest

# Update docker-compose.yml
services:
  recipes-api:
    image: ghcr.io/<your-username>/jarvis-recipes-server:latest
    # ... rest of config
```

### Pinning to Specific Version

```bash
# docker-compose.yml
services:
  recipes-api:
    image: ghcr.io/<your-username>/jarvis-recipes-server:1.2.3
    # ... rest of config
```

## 📝 Creating a Production Release

1. **Commit and push your changes to `main`**:
   ```bash
   git add .
   git commit -m "feat: add new feature"
   git push origin main
   ```
   → This triggers a `dev` build

2. **Create and push a version tag**:
   ```bash
   git tag v1.0.0
   git push origin v1.0.0
   ```
   → This triggers a production build with tags: `1.0.0`, `1.0`, `latest`

## 🔐 Permissions

The workflow uses `GITHUB_TOKEN` (automatically provided) to push to GHCR. No additional secrets needed!

## 💡 Tips

### Semantic Versioning
Follow [semver](https://semver.org/) for tags:
- `v1.0.0` - Major.Minor.Patch
- **Major**: Breaking changes
- **Minor**: New features (backward compatible)
- **Patch**: Bug fixes

### Testing Dev Builds First
Always test with `:dev` before creating a production tag:

```bash
# 1. Push to main
git push origin main

# 2. Wait for dev build to complete
# 3. Test with :dev tag
docker-compose pull && docker-compose up

# 4. If tests pass, create release tag
git tag v1.0.0 && git push origin v1.0.0
```

### Rollback to Previous Version
If a release has issues:

```bash
# Use previous version tag
docker pull ghcr.io/<your-username>/jarvis-recipes-server:1.0.1
```

Or revert the tag:

```bash
git tag -d v1.0.2
git push origin :refs/tags/v1.0.2
```

## 🛠️ Build Times

- **Dev builds**: ~5-10 minutes (with cache)
- **Production builds**: ~5-10 minutes (with cache)
- Multi-platform adds ~2-3 minutes

## 📊 Monitoring

Check build status at:
```
https://github.com/<your-username>/jarvis-recipes-server/actions
```

View published images at:
```
https://github.com/<your-username>/jarvis-recipes-server/pkgs/container/jarvis-recipes-server
```

