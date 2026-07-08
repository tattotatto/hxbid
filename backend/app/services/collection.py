"""宏曦标书 - Information Collection Service.

Analyses parsed tender requirements, matches them against the resource
library (qualifications, personnel, company profile), and manages
project-resource assignments.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import json
import logging
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.company_profile import CompanyProfile
from app.models.personnel import Personnel
from app.models.project import BidProject
from app.models.project_resource import ProjectPersonnel, ProjectQualification
from app.models.qualification import Qualification

logger = logging.getLogger(__name__)


# ── Auto-match against resource library ────────────────────────────────


async def analyze_collection_needs(
    project_id: str,
    db: AsyncSession,
) -> Dict[str, Any]:
    """Analyse a project's parsed requirements and match against the library.

    Returns a dict with ``document_items`` and ``personnel_items`` lists,
    each entry containing the requirement, whether it was matched, and the
    matching resources.
    """
    project = await db.get(BidProject, project_id)
    if not project:
        raise ValueError(f"Project {project_id} not found")

    try:
        reqs = json.loads(project.parsed_requirements_json or "{}")
    except json.JSONDecodeError:
        reqs = {}

    # Load library data
    quals = (await db.execute(select(Qualification))).scalars().all()
    personnel_list = (
        (await db.execute(select(Personnel))).scalars().all()
    )
    company = (
        (await db.execute(select(CompanyProfile).limit(1)))
        .scalars()
        .one_or_none()
    )

    document_items = []
    personnel_items = []

    # ── Required documents ──
    required_docs = reqs.get("required_documents", [])
    for doc in required_docs:
        name = doc["name"] if isinstance(doc, dict) else str(doc)
        category = doc.get("category", "other") if isinstance(doc, dict) else "other"

        matches = _match_document(name, category, quals, company)
        document_items.append({
            "requirement": {"name": name, "category": category},
            "matched": len(matches) > 0,
            "matches": matches,
            "match_status": "matched" if matches else "missing",
        })

    # ── Required personnel ──
    required_personnel = reqs.get("required_personnel", [])
    for p_req in required_personnel:
        role = p_req.get("role", "") if isinstance(p_req, dict) else str(p_req)
        certs = p_req.get("certifications", []) if isinstance(p_req, dict) else []
        count = p_req.get("count", 1) if isinstance(p_req, dict) else 1

        matches = _match_personnel(role, certs, personnel_list)
        personnel_items.append({
            "requirement": {
                "name": role,
                "category": "personnel",
                "details": f"需{cert_count}人" if count > 1 else "",
            },
            "matched": len(matches) > 0,
            "matches": matches,
            "match_status": "matched" if matches else "missing",
        })

    # ── Determine completeness ──
    all_matched = all(
        item["match_status"] == "matched"
        for item in document_items + personnel_items
    )

    return {
        "project_id": project_id,
        "status": project.status,
        "document_items": document_items,
        "personnel_items": personnel_items,
        "is_complete": all_matched,
    }


def _match_document(
    name: str,
    category: str,
    quals: List[Qualification],
    company: CompanyProfile | None,
) -> List[Dict[str, Any]]:
    """Try to match a required document against the library."""
    matches = []

    # Check company profile for common items
    if company:
        company_items = {
            "营业执照": company.business_license_number,
            "法定代表人身份证": company.legal_rep_id_number,
        }
        for label, value in company_items.items():
            if _fuzzy_match(name, label) and value:
                matches.append({
                    "source": "company",
                    "name": label,
                    "detail": value,
                })

    # Check qualifications
    for q in quals:
        if _fuzzy_match(name, q.name):
            matches.append({
                "source": "qualification",
                "id": q.id,
                "name": q.name,
                "cert_number": q.cert_number or "",
                "issuing_authority": q.issuing_authority or "",
            })

    return matches


def _match_personnel(
    role: str,
    certs: List[str],
    personnel_list: List[Personnel],
) -> List[Dict[str, Any]]:
    """Try to match a personnel requirement against the library."""
    matches = []

    for p in personnel_list:
        tags = (p.tags or "").lower()
        # Match by tags containing role keywords or certifications
        role_keywords = role.lower().replace("项目", "").replace("负责人", "").replace("人员", "")
        if role_keywords and role_keywords in tags:
            matches.append(_personnel_to_dict(p))
            continue

        for cert in certs:
            if cert.lower() in tags:
                matches.append(_personnel_to_dict(p))
                break

    # If no tag-based match, return all personnel as candidates
    if not matches and personnel_list:
        matches = [_personnel_to_dict(p) for p in personnel_list[:5]]

    return matches


def _personnel_to_dict(p: Personnel) -> Dict[str, Any]:
    return {
        "source": "personnel",
        "id": p.id,
        "name": p.name,
        "education": p.education or "",
        "phone": p.phone or "",
        "tags": p.tags or "",
    }


def _fuzzy_match(needle: str, haystack: str) -> bool:
    """Simple substring-based fuzzy match."""
    n = needle.lower().replace(" ", "").replace("（", "(").replace("）", ")")
    h = haystack.lower().replace(" ", "").replace("（", "(").replace("）", ")")
    return n in h or h in n


# ── Assignment operations ────────────────────────────────────────────────


async def assign_personnel(
    project_id: str,
    personnel_id: str,
    role: str,
    requirement_desc: str,
    db: AsyncSession,
) -> ProjectPersonnel:
    """Assign a personnel record to a project with a specific role."""
    # Remove any previous assignment for the same role in this project
    existing = await db.execute(
        select(ProjectPersonnel).where(
            ProjectPersonnel.project_id == project_id,
            ProjectPersonnel.role == role,
        )
    )
    for old in existing.scalars():
        await db.delete(old)

    pp = ProjectPersonnel(
        project_id=project_id,
        personnel_id=personnel_id,
        role=role,
        requirement_desc=requirement_desc,
        match_status="assigned",
    )
    db.add(pp)
    await db.flush()
    await db.refresh(pp)
    return pp


async def unassign_personnel(
    project_id: str,
    pp_id: str,
    db: AsyncSession,
) -> None:
    """Remove a personnel assignment from a project."""
    pp = await db.get(ProjectPersonnel, pp_id)
    if pp and pp.project_id == project_id:
        await db.delete(pp)
        await db.flush()


async def link_qualification(
    project_id: str,
    qualification_id: str,
    requirement_name: str,
    db: AsyncSession,
) -> ProjectQualification:
    """Link an existing qualification to a project."""
    pq = ProjectQualification(
        project_id=project_id,
        qualification_id=qualification_id,
        requirement_name=requirement_name,
        match_status="matched",
    )
    db.add(pq)
    await db.flush()
    await db.refresh(pq)
    return pq


async def upload_qualification(
    project_id: str,
    requirement_name: str,
    file_path: str,
    db: AsyncSession,
) -> ProjectQualification:
    """Record an uploaded file for a missing qualification requirement."""
    pq = ProjectQualification(
        project_id=project_id,
        requirement_name=requirement_name,
        match_status="uploaded",
        uploaded_file_path=file_path,
    )
    db.add(pq)
    await db.flush()
    await db.refresh(pq)
    return pq


async def confirm_collection(project_id: str, db: AsyncSession) -> BidProject:
    """Mark collection as complete and advance the project to 'parsed'."""
    project = await db.get(BidProject, project_id)
    if not project:
        raise ValueError(f"Project {project_id} not found")
    project.status = "parsed"
    await db.flush()
    await db.refresh(project)
    return project


async def get_collected_resources(
    project_id: str,
    db: AsyncSession,
) -> Dict[str, List[Dict[str, Any]]]:
    """Return collected qualifications, personnel, and company profile for a project.

    Used by the generation step to inject collected resources directly
    instead of running keyword-based RAG matching.
    """
    # Qualifications
    pq_result = await db.execute(
        select(ProjectQualification)
        .where(ProjectQualification.project_id == project_id)
        .options(selectinload(ProjectQualification.qualification))
    )
    quals = []
    for pq in pq_result.scalars():
        q = pq.qualification
        quals.append({
            "name": q.name if q else pq.requirement_name,
            "cert_number": q.cert_number if q else "",
            "issuing_authority": q.issuing_authority if q else "",
            "attachment_path": q.attachment_path if q else "",
            "source": "collected",
        })

    # Personnel
    pp_result = await db.execute(
        select(ProjectPersonnel)
        .where(ProjectPersonnel.project_id == project_id)
        .options(selectinload(ProjectPersonnel.personnel))
    )
    personnel = []
    for pp in pp_result.scalars():
        p = pp.personnel
        personnel.append({
            "id": pp.id,
            "name": p.name if p else "(未指定)",
            "role": pp.role,
            "education": p.education if p else "",
            "tags": p.tags if p else "",
            "source": "collected",
        })

    # Company profile
    cp_result = await db.execute(select(CompanyProfile).limit(1))
    cp = cp_result.scalar_one_or_none()
    company = None
    if cp:
        company = {
            "company_name": cp.company_name or "",
            "business_license_number": cp.business_license_number or "",
            "legal_rep_name": cp.legal_rep_name or "",
            "legal_rep_id_number": cp.legal_rep_id_number or "",
            "address": cp.address or "",
            "contact_phone": cp.contact_phone or "",
            "website": cp.website or "",
            "notes": cp.notes or "",
        }

    return {"qualifications": quals, "personnel": personnel, "company": company}
