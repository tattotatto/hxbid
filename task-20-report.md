# Task 20: Docker Compose 部署

**Status:** Complete
**Date:** 2026-06-22

## Files Created

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Multi-service orchestration (db, backend, frontend, nginx) |
| `nginx.conf` | Reverse proxy routing /api/ to backend, / to frontend |
| `backend/Dockerfile` | Python 3.12-slim with uvicorn on port 8000 |
| `frontend/Dockerfile` | Multi-stage build: Node builder then nginx serving dist/ |

## Files Modified

| File | Change |
|------|--------|
| `backend/app/config.py` | Added `field_validator` import and `parse_cors_origins` validator to handle CORS_ORIGINS as JSON string from env vars |
| `.gitignore` | Added `*.env` pattern alongside existing `.env`, added `outputs/` (was already present with `uploads/` and `templates/`) |

## Services

1. **db** - PostgreSQL 15 Alpine, user `hongxi`, DB `hongxi_bid`, healthcheck on pg_isready
2. **backend** - FastAPI app, depends on healthy db, volumes for uploads/outputs
3. **frontend** - React SPA built with multi-stage Docker, served by nginx
4. **nginx** - Reverse proxy on port 80, proxies `/api/` to backend:8000 and `/` to frontend:80

## Key Configuration Details

- CORS_ORIGINS set to `["http://localhost"]` in production docker-compose
- SECRET_KEY uses env variable with fallback `change-me-in-production`
- DEEPSEEK_API_KEY, WECOM_WEBHOOK_URL, DINGTALK_WEBHOOK_URL read from host environment
- client_max_body_size 100m in nginx for large file uploads
- proxy_read_timeout 600s for long-running AI generation requests
- Docker volumes: pgdata, uploads, outputs (persist across restarts)

## Verification

- docker-compose.yml YAML syntax validated successfully
- config.py Python syntax verified (field_validator added correctly)
- package-lock.json confirmed present for npm ci in frontend Dockerfile
- Vite outputs to dist/ by default (compatible with Dockerfile COPY)
