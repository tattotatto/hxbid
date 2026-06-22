# Task 6 Report: 资质CRUD API

## Status: COMPLETE

## Files Created

### `backend/app/schemas/qualification.py`
Pydantic schemas for qualification data:
- `QualificationCreate` - fields: name, cert_number, issuing_authority, issue_date, expiry_date, notes
- `QualificationUpdate` - all fields Optional, uses `exclude_unset=True` for partial updates
- `QualificationRead` - all DB fields including id, attachment_path, created_at, updated_at; uses `from_attributes=True`

### `backend/app/api/qualifications.py`
Full CRUD router with 5 endpoints, all protected by `get_current_user`:

| Method | Path | Status | Description |
|--------|------|--------|-------------|
| GET | / | 200 | List all quals, ordered by created_at desc |
| POST | / | 201 | Create a new qualification |
| GET | /{qual_id} | 200/404 | Get one qualification by ID |
| PUT | /{qual_id} | 200/404 | Update qualification (partial, exclude_unset) |
| DELETE | /{qual_id} | 204/404 | Delete qualification |

## Files Modified

### `backend/app/api/router.py`
- Uncommented: `from app.api.qualifications import router as qual_router`
- Uncommented: `api_router.include_router(qual_router, prefix="/qualifications", tags=["资质管理"])`

## Verification

```
python -c "from app.api.router import api_router; print('Router OK')"   -> Router OK
python -c "from app.schemas.qualification import QualificationCreate, QualificationRead; print('Schemas OK')"   -> Schemas OK
```

## Commit

`7c2f3c6` - feat: add qualification CRUD API (Task 6)
