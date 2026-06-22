# Task 4 Report: JWT Security & PII De-identification

**Date:** 2026-06-22
**Status:** Complete

## Files Created

| File | Purpose |
|------|---------|
| `backend/app/utils/__init__.py` | Utilities package marker |
| `backend/app/utils/security.py` | JWT token creation, bcrypt password hashing, `get_current_user` dependency |
| `backend/app/services/__init__.py` | Services package marker |
| `backend/app/services/deid.py` | PII de-identification / re-identification service |

## Verification Results

### deid.py (stdlib-only, verified)

- `deidentify_text()` correctly replaces ID numbers, phone numbers, names, and certificate numbers with placeholders.
- `reidentify_docx()` correctly restores all placeholders to original values.
- Roundtrip test: **PASS** -- `reidentify_docx(deidentify_text(text)) == text`

### security.py (requires dependencies)

- Code is correct per the task brief specification.
- Modules needed: `python-jose[cryptography]==3.3.0` and `passlib[bcrypt]==1.7.4` (already declared in `requirements.txt`).
- Verification blocked: dependencies not installed in current environment and pip install was denied.
- To verify manually: `cd backend && pip install -r requirements.txt && python -c "from app.utils.security import create_access_token, get_password_hash; print('Security OK')"`

## Exported Functions

**`app.utils.security`:**
- `verify_password(plain, hashed) -> bool` -- bcrypt verify
- `get_password_hash(password) -> str` -- bcrypt hash
- `create_access_token(data, expires_delta=None) -> str` -- JWT with HS256, sub+exp claims
- `get_current_user(token, db) -> User` -- FastAPI dependency, raises 401 on invalid/missing token

**`app.services.deid`:**
- `deidentify_text(text) -> Tuple[str, Dict[str, str]]` -- replaces PII with 【TYPE#】 placeholders
- `reidentify_docx(text, mapping) -> str` -- restores placeholders

## Git Commit

`4a07f3b` -- Task 4: JWT security utilities and PII de-identification service (4 files, 168 insertions)
