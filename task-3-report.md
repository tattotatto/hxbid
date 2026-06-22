# Task 3 Report: 资源库模型 -- 资质、人员、标书项目

**Status:** COMPLETE  
**Date:** 2026-06-22  
**Commit:** 30a4506

---

## Files Created

### 1. `backend/app/models/qualification.py`
- **Model:** `Qualification` (table: `qualifications`)
- Fields: id, name, cert_number, issuing_authority, issue_date, expiry_date, attachment_path, notes, created_at, updated_at
- Uses `Mapped` + `mapped_column` style (SQLAlchemy 2.0)

### 2. `backend/app/models/personnel.py`
- **Model:** `Personnel` (table: `personnel`)
  - Fields: id, name, id_card, education, phone, tags, created_at, updated_at
  - Relationships: `experiences` -> PersonnelExperience, `certificates` -> PersonnelCertificate (both cascade delete-orphan)
- **Model:** `PersonnelExperience` (table: `personnel_experiences`)
  - Fields: id, personnel_id (FK), start_date, end_date, organization, position, project_scale, responsibilities, achievements
  - Relationship: `personnel` -> Personnel
- **Model:** `PersonnelCertificate` (table: `personnel_certificates`)
  - Fields: id, personnel_id (FK), cert_name, cert_number, issuing_authority, issue_date, expiry_date
  - Relationship: `personnel` -> Personnel

### 3. `backend/app/models/project.py`
- **Model:** `BidProject` (table: `bid_projects`)
  - Fields: id, name, bid_deadline, status (default "draft"), bid_result (default "pending"), original_file_path, parsed_requirements_json (default "{}"), outline_json (default "[]"), created_by (FK -> users.id), created_at, updated_at
  - Relationships: `chapters` -> ProjectChapter (cascade delete-orphan)
- **Model:** `ProjectChapter` (table: `project_chapters`)
  - Fields: id, project_id (FK), title, order_index, ai_generated_content, final_content, status (default "pending")
  - Relationship: `project` -> BidProject

## Files Modified

### 4. `backend/app/models/__init__.py`
- Uncommented imports and added all 6 new models to `__all__`

## Verification

```
python -c "from app.models import Qualification, Personnel, PersonnelExperience, PersonnelCertificate, BidProject, ProjectChapter; print('All 6 models OK')"
Output: All 6 models OK
```

All models use `Base` from `app.database`, follow `Mapped` + `mapped_column` style, and include `__repr__` methods consistent with the existing `User` model.
