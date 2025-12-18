# Docker Build Troubleshooting

## Problem: "No space left on device" in GitHub Actions

This error occurs when building multi-platform Docker images with large ML dependencies (torch, scipy, EasyOCR) on GitHub Actions runners.

### Root Causes:
1. **Limited disk space**: GitHub Actions runners have ~14GB free space
2. **Multi-platform builds**: Building for both `linux/amd64` and `linux/arm64` uses 2x+ space
3. **Large ML dependencies**: PyTorch, SciPy, and EasyOCR models are several GB each
4. **Build cache**: Docker layers and Poetry cache accumulate quickly

---

## ✅ Applied Fixes

### 1. Free Up Disk Space Before Build
**File**: `.github/workflows/docker-build-push.yml`

Added cleanup step that removes:
- .NET tools (~2GB)
- Android SDK (~8GB)
- GHC/Haskell tools (~1GB)
- CodeQL tools (~5GB)
- Boost libraries (~1GB)
- Unused Docker data

**Result**: Frees up ~15-20GB before starting the build

### 2. Optimized Dockerfile
**File**: `Dockerfile`

Changes:
- Combined RUN commands to reduce layers
- Split dependency installation from code copy (better caching)
- Added aggressive cleanup after poetry install:
  - Purge pip cache
  - Remove poetry cache
  - Delete Python bytecode files
- Install dependencies with `--no-root` first, then `--only-root` separately
- Pin poetry version for consistency

**Result**: Smaller image layers, less build-time disk usage

### 3. Added .dockerignore
**File**: `.dockerignore`

Excludes from build context:
- `.git/` directory
- Test files
- Documentation
- Virtual environments
- IDE files
- Media uploads

**Result**: Smaller build context sent to Docker daemon

---

## 🔄 If Issues Persist

### Option A: Build AMD64 Only (Recommended for Servers)

If multi-platform builds still fail, switch to AMD64-only:

```bash
# Rename the current workflow
mv .github/workflows/docker-build-push.yml .github/workflows/docker-build-push.yml.disabled

# Enable AMD64-only workflow
mv .github/workflows/docker-build-push-amd64-only.yml.disabled .github/workflows/docker-build-push-amd64-only.yml
```

**Pros:**
- Faster builds (5-10 min instead of 15-20 min)
- Uses half the disk space
- Works on most servers (AWS, GCP, DigitalOcean)

**Cons:**
- Mac M1/M2/M3 users must build locally

### Option B: Build Locally for Mac

Mac users can always build locally for ARM64:

```bash
# Build for your architecture
docker build -t myapp:latest .

# Or explicitly for ARM64
docker buildx build --platform linux/arm64 -t myapp:latest .
```

### Option C: Use GitHub Actions Self-Hosted Runner

Set up a self-hosted runner with more disk space:
- Use a machine with 50GB+ free space
- Configure as self-hosted runner in GitHub settings
- Update workflow to use: `runs-on: self-hosted`

---

## 📊 Monitoring Disk Usage

During builds, you can monitor disk usage in the GitHub Actions logs:

```yaml
- name: Check disk space
  run: df -h
```

Look for the `/` mount point usage percentage.

---

## 🚀 Build Time Expectations

### Multi-Platform (AMD64 + ARM64)
- **First build**: 15-20 minutes
- **With cache**: 8-12 minutes
- **Disk usage**: ~20-25GB peak

### AMD64 Only
- **First build**: 8-12 minutes
- **With cache**: 4-8 minutes
- **Disk usage**: ~10-15GB peak

---

## 💡 Additional Optimizations (Future)

If you still need more space:

1. **Build platforms separately**: Split into two jobs, one per platform
2. **Use Docker Hub**: More generous disk limits (but slower)
3. **Reduce dependencies**: Remove unused ML models/libraries
4. **Multi-stage builds**: Use builder pattern to reduce final image size
5. **External storage**: Store models in S3/GCS, download at runtime

---

## 🔍 Debugging Tips

### Check Available Space
```yaml
- name: Show disk usage
  run: |
    df -h
    du -sh /var/lib/docker
    docker system df
```

### Test Build Locally
```bash
# Simulate GitHub Actions environment
docker buildx create --use --name multiplatform
docker buildx build --platform linux/amd64,linux/arm64 -t test:latest .
```

### Check Image Size
```bash
docker images myapp:latest --format "{{.Size}}"
```

---

## 📝 Current Configuration

**Workflow**: Multi-platform (AMD64 + ARM64)  
**Cleanup**: Enabled (~15-20GB freed)  
**Dockerfile**: Optimized with layer cleanup  
**Build Context**: Filtered via .dockerignore  

**Estimated disk usage during build**: ~20GB  
**GitHub Actions runner capacity**: ~25-30GB after cleanup  
**Safety margin**: ~5-10GB ✅

The build should now succeed! If not, switch to AMD64-only builds.

