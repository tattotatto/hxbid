# Task 7 Report: 人员CRUD API

**Status:** Complete
**Date:** 2026-06-22

## Files Created

### 1. `backend/app/schemas/personnel.py`
Pydantic schemas for personnel, experiences, and certificates:
- `ExperienceCreate` / `ExperienceRead` - nested work experience schemas
- `CertificateCreate` / `CertificateRead` - nested certificate schemas
- `PersonnelCreate` - accepts nested `experiences` and `certificates` lists
- `PersonnelUpdate` - basic field partial updates (name, id_card, education, phone, tags)
- `PersonnelRead` - full read model with nested relations and timestamps

### 2. `backend/app/api/personnel.py`
CRUD API routes, all protected by `get_current_user` dependency:
- `GET /` - list all personnel with `selectinload` eager loading of experiences and certificates
- `POST /` - create personnel with nested experiences and certificates (flush then add children)
- `GET /{p_id}` - get single personnel with relations; 404 if missing
- `PUT /{p_id}` - update basic fields only via `exclude_unset`; 404 if missing
- `DELETE /{p_id}` - delete with cascade (handled by model relationships); 404 if missing
- `_get_with_relations(p_id, db)` - helper to fetch personnel with eager-loaded relations

## Files Modified

### 3. `backend/app/api/router.py`
- Imported `personnel_router` from `app.api.personnel`
- Registered router at prefix `/personnel` with tag `人员管理`

## Verification

```
python -c "from app.schemas.personnel import PersonnelCreate, PersonnelRead; print('Schemas OK')"
# Output: Schemas OK

python -c "from app.api.personnel import router; print('Router OK')"
# Output: Router OK
```

## Commit

```
b7bfc0a Task 7: 人员CRUD API - schemas, routes, and router registration
```
