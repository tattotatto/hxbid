# Task 8 Report: 项目管理API

## Summary
Implemented the project management API (任务8 of 宏曦标书 MVP).

## Files Changed

### Created
- `backend/app/schemas/project.py` — Pydantic schemas: ProjectCreate, ProjectUpdate, ProjectRead, ChapterRead, ChapterUpdate
- `backend/app/api/projects.py` — CRUD API routes for bid projects and chapter editing

### Modified
- `backend/app/api/router.py` — Activated projects router at `/api/v1/projects`

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/projects/` | List all projects with chapters (created_at desc) |
| POST | `/api/v1/projects/` | Create a new project |
| GET | `/api/v1/projects/{id}` | Get project with chapters (404 if missing) |
| PUT | `/api/v1/projects/{id}` | Update project fields (exclude_unset) |
| DELETE | `/api/v1/projects/{id}` | Delete project (cascade chapters) |
| PUT | `/api/v1/projects/{id}/chapters/{cid}` | Update chapter final_content/status |

## Verification
- `from app.schemas.project import ProjectCreate, ProjectRead` — OK
- `from app.api.projects import router` — OK
- `from app.api.router import api_router` — OK
- `from app.main import app` — OK
