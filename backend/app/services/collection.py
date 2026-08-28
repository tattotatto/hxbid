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
from app.models.project_resource import ProjectContract, ProjectPersonnel, ProjectQualification
from app.models.qualification import Qualification

logger = logging.getLogger(__name__)


# ── Auto-match against resource library ────────────────────────────────


_PERFORMANCE_KEYWORDS = ("业绩", "合同", "类似项目", "中标", "履约")


def _is_performance_requirement(name: str, category: str) -> bool:
    """业绩/合同类需求判定（统一口径，三处消费）.

    注意：parse 侧（AI 提取 required_documents）从未产出 contract_performance
    分类——业绩需求实际落 category=other。若只认 category==contract_performance，
    用户手动链接的合同（ProjectContract）在资料汇总/占用时全部查不到。因此
    按需求名关键词兜底：含业绩/合同/类似项目/中标/履约即按合同资源处理。
    """
    if category == "contract_performance":
        return True
    return any(k in name for k in _PERFORMANCE_KEYWORDS)


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

    from app.models.contract import Contract

    contracts = (
        (await db.execute(select(Contract))).scalars().all()
    )

    # ── 加载本项目已落库选择（用户手动链接 + 历史 auto）──
    pq_rows = (await db.execute(
        select(ProjectQualification)
        .where(ProjectQualification.project_id == project_id)
        .options(selectinload(ProjectQualification.qualification))
    )).scalars().all()
    pp_rows = (await db.execute(
        select(ProjectPersonnel)
        .where(ProjectPersonnel.project_id == project_id)
        .options(selectinload(ProjectPersonnel.personnel))
    )).scalars().all()
    pc_rows = (await db.execute(
        select(ProjectContract)
        .where(ProjectContract.project_id == project_id)
        .options(selectinload(ProjectContract.contract))
    )).scalars().all()

    document_items = []
    personnel_items = []

    # ── Required documents ──
    required_docs = reqs.get("required_documents", [])
    for doc in required_docs:
        name = doc["name"] if isinstance(doc, dict) else str(doc)
        category = doc.get("category", "other") if isinstance(doc, dict) else "other"

        if _is_performance_requirement(name, category):
            auto = _match_contracts(name, contracts)
            persisted = [
                {
                    "source": "contract", "selection": "selected",
                    "id": pc.contract_id, "link_id": pc.id,
                    "name": pc.contract.project_name if pc.contract else pc.requirement_name,
                    "procurement_unit": pc.contract.procurement_unit if pc.contract else "",
                    "contract_amount": pc.contract.contract_amount if pc.contract else "",
                    "contract_date": str(pc.contract.contract_date) if pc.contract and pc.contract.contract_date else "",
                }
                for pc in pc_rows if pc.requirement_name == name
            ]
            matches, match_status = _merge_matches(persisted, auto, "contract")
            # 自动候选标 selection=auto（与 qualification/personnel 分支一致）
            if match_status in ("auto", "matched"):
                for m in matches:
                    m.setdefault("selection", "auto")
        else:
            auto = _match_document(name, category, quals, company)
            persisted = [
                {
                    "source": "qualification", "selection": "selected",
                    "id": pq.qualification_id, "link_id": pq.id,
                    "name": pq.qualification.name if pq.qualification else pq.requirement_name,
                    "cert_number": pq.qualification.cert_number if pq.qualification else "",
                    "issuing_authority": pq.qualification.issuing_authority if pq.qualification else "",
                }
                for pq in pq_rows if pq.requirement_name == name
            ]
            matches, match_status = _merge_matches(persisted, auto, category)
            # 自动候选标 selection=auto（已落库选择在 persisted 里已是 selected）
            if match_status in ("auto", "matched"):
                for m in matches:
                    m.setdefault("selection", "auto")

        document_items.append({
            "requirement": {"name": name, "category": category},
            "matched": match_status in ("selected", "auto", "uploaded"),
            "matches": matches,
            "match_status": match_status,
        })

    # ── Required personnel ──
    required_personnel = reqs.get("required_personnel", [])
    for p_req in required_personnel:
        role = p_req.get("role", "") if isinstance(p_req, dict) else str(p_req)
        certs = p_req.get("certifications", []) if isinstance(p_req, dict) else []
        count = p_req.get("count", 1) if isinstance(p_req, dict) else 1

        auto = _match_personnel(role, certs, personnel_list)
        persisted = [
            {
                "source": "personnel", "selection": "selected",
                "id": pp.personnel_id, "link_id": pp.id,
                "name": pp.personnel.name if pp.personnel else "(未指定)",
                "education": pp.personnel.education if pp.personnel else "",
                "tags": pp.personnel.tags if pp.personnel else "",
                "role": pp.role,
            }
            for pp in pp_rows if pp.role == role
        ]
        matches, match_status = _merge_matches(persisted, auto, "personnel")
        if match_status in ("auto", "matched"):
            for m in matches:
                m.setdefault("selection", "auto")
        personnel_items.append({
            "requirement": {
                "name": role,
                "category": "personnel",
                "details": f"需{count}人" if count > 1 else "",
                "count": count,
            },
            "matched": match_status in ("selected", "auto", "uploaded"),
            "matches": matches,
            "match_status": match_status,
        })

    # ── Determine completeness ──
    all_matched = all(
        item["match_status"] in ("selected", "auto", "uploaded")
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
                "confidence": "high",
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
            matches.append(_personnel_to_dict(p, confidence="high"))
            continue

        for cert in certs:
            if cert.lower() in tags:
                matches.append(_personnel_to_dict(p, confidence="high"))
                break

    # If no tag-based match, return all personnel as candidates
    if not matches and personnel_list:
        matches = [_personnel_to_dict(p, confidence="low") for p in personnel_list[:5]]

    return matches


def _match_contracts(
    requirement_name: str,
    contracts: List,
) -> List[Dict[str, Any]]:
    """Match a contract-performance requirement against the contract library.

    Extracts date-filter keywords ("2023") and service-type keywords
    ("安保", "物业", "保洁", etc.) from the requirement name, then filters
    contracts by contract_date range and fuzzy-matches procurement_content.
    """
    from datetime import date as date_type

    matches = []
    req_lower = requirement_name.lower()

    # Extract year hints, e.g. "2023年1月1日至投标截止日"
    year_hints = []
    import re
    year_matches = re.findall(r"(\d{4})\s*年", requirement_name)
    for y in year_matches:
        try:
            year_hints.append(int(y))
        except ValueError:
            pass

    # Extract service type keywords
    SERVICE_KEYWORDS = ["安保", "保安", "物业", "保洁", "绿化", "餐饮", "维修", "后勤", "秩序维护"]
    matched_service = None
    for kw in SERVICE_KEYWORDS:
        if kw in requirement_name:
            matched_service = kw
            break

    for c in contracts:
        # Date filter: contract_date >= earliest year hint
        if year_hints and c.contract_date:
            min_year = min(year_hints)
            if c.contract_date < date_type(min_year, 1, 1):
                continue

        # Service type fuzzy match against procurement_content
        if matched_service:
            content = (c.procurement_content or "").lower()
            if matched_service not in content and matched_service not in (c.project_name or "").lower():
                continue

        matches.append({
            "source": "contract",
            "id": c.id,
            "name": c.project_name,
            "procurement_unit": c.procurement_unit or "",
            "contract_amount": c.contract_amount or "",
            "contract_date": str(c.contract_date) if c.contract_date else "",
            "service_period": c.service_period or "",
            "confidence": "high",
        })

    # If no date-filtered match, return all as candidates
    if not matches and contracts:
        matches = [
            {
                "source": "contract",
                "id": c.id,
                "name": c.project_name,
                "procurement_unit": c.procurement_unit or "",
                "contract_amount": c.contract_amount or "",
                "contract_date": str(c.contract_date) if c.contract_date else "",
                "service_period": c.service_period or "",
                "confidence": "low",
            }
            for c in contracts[:10]
        ]

    return matches


def _personnel_to_dict(p: Personnel, confidence: str = "high") -> Dict[str, Any]:
    return {
        "source": "personnel",
        "id": p.id,
        "name": p.name,
        "education": p.education or "",
        "phone": p.phone or "",
        "tags": p.tags or "",
        "confidence": confidence,
    }


def _fuzzy_match(needle: str, haystack: str) -> bool:
    """Simple substring-based fuzzy match."""
    n = needle.lower().replace(" ", "").replace("（", "(").replace("）", ")")
    h = haystack.lower().replace(" ", "").replace("（", "(").replace("）", ")")
    return n in h or h in n


def _merge_matches(
    persisted: list[dict],
    auto: list[dict],
    category: str,
) -> tuple[list[dict], str]:
    """合并已落库选择与自动匹配候选.

    Returns:
        (matches, match_status):
        - 有已落库选择 → (persisted, "selected")
        - 无已落库、有确定性自动候选 → (auto, "auto")
        - 无已落库、仅有非确定性候选 → (auto, "matched")
        - 全无 → ([], "missing")
    """
    if persisted:
        return persisted, "selected"
    if not auto:
        return [], "missing"
    if _is_confident_auto(category, auto):
        return auto, "auto"
    return auto, "matched"


def _is_confident_auto(category: str, auto: list[dict]) -> bool:
    """自动匹配是否确定性（可安全自动占用）.

    确定性条件：
    - 候选带真实资源 id（排除公司资料无 id 的匹配，如营业执照）
    - 候选带 confidence="high"（排除人员"无标签返回前 N 人"、合同
      "无年份/服务关键词返回前 N 条"的兜底分支）
    """
    if not auto:
        return False
    first = auto[0]
    if not first.get("id"):
        return False
    return first.get("confidence") == "high"


def _pick_auto_occupy(document_items: list, personnel_items: list) -> list[dict]:
    """从三态条目中选出需要自动占用的行（纯计算，不触库）.

    每个需求按容量封顶：
    - qualification/contract 需求 count=1，仅取首个 high-confidence 匹配；
    - personnel 需求 count=item["requirement"].get("count", 1)，取前 count 个。

    Returns:
        list of {"model", "requirement_name", "resource_id", "count"}
        model ∈ {"qualification", "personnel", "contract"}
    """
    rows: list[dict] = []
    for item in document_items:
        if item.get("match_status") != "auto":
            continue
        req_name = item["requirement"]["name"]
        category = item["requirement"].get("category", "other")
        for m in item.get("matches", []):
            if not m.get("id") or m.get("confidence") != "high":
                continue
            model = "contract" if _is_performance_requirement(req_name, category) else "qualification"
            rows.append({"model": model, "requirement_name": req_name, "resource_id": m["id"], "count": 1})
            break  # 文档类需求容量恒为 1
    for item in personnel_items:
        if item.get("match_status") != "auto":
            continue
        role = item["requirement"]["name"]
        count = item["requirement"].get("count", 1)
        taken = 0
        for m in item.get("matches", []):
            if not m.get("id") or m.get("confidence") != "high":
                continue
            rows.append({"model": "personnel", "requirement_name": role, "resource_id": m["id"], "count": count})
            taken += 1
            if taken >= count:
                break
    return rows


async def _auto_occupy_confident_matches(
    project_id: str,
    rows: list[dict],
    db: AsyncSession,
) -> int:
    """按 _pick_auto_occupy 的结果批量落库（幂等 + 容量封顶）.

    rows 由 _pick_auto_occupy 产出、按需求连续分组。对每个 (model,
    requirement_name) 只查一次既有行（查询发生在该需求任何 add 之前，
    故 SELECT 只命中「调用前已落库」的行，不叠加 SQLAlchemy autoflush
    把本批新行刷进库造成的双计数），再按 count 封顶插入前 N 个。
    """
    occupied = 0
    current_key: tuple[str, str] | None = None
    existing_count = 0
    remaining = 0
    inserted = 0

    for spec in rows:
        model_name = spec["model"]
        req_name = spec["requirement_name"]
        capacity = spec["count"]
        key = (model_name, req_name)

        if key != current_key:
            # 换到新需求：首次查询既有行（本需求尚未 add，只命中既有行）
            current_key = key
            if model_name == "qualification":
                existing_count = len((await db.execute(
                    select(ProjectQualification).where(
                        ProjectQualification.project_id == project_id,
                        ProjectQualification.requirement_name == req_name,
                    )
                )).scalars().all())
            elif model_name == "personnel":
                existing_count = len((await db.execute(
                    select(ProjectPersonnel).where(
                        ProjectPersonnel.project_id == project_id,
                        ProjectPersonnel.role == req_name,
                    )
                )).scalars().all())
            else:
                existing_count = len((await db.execute(
                    select(ProjectContract).where(
                        ProjectContract.project_id == project_id,
                        ProjectContract.requirement_name == req_name,
                    )
                )).scalars().all())
            remaining = max(0, capacity - existing_count)
            inserted = 0

        if inserted >= remaining:
            continue

        if model_name == "qualification":
            db.add(ProjectQualification(
                project_id=project_id,
                qualification_id=spec["resource_id"],
                requirement_name=req_name,
                match_status="matched",
            ))
        elif model_name == "personnel":
            db.add(ProjectPersonnel(
                project_id=project_id,
                personnel_id=spec["resource_id"],
                role=req_name,
                requirement_desc=req_name,
                match_status="assigned",
            ))
        else:
            db.add(ProjectContract(
                project_id=project_id,
                contract_id=spec["resource_id"],
                requirement_name=req_name,
                match_status="matched",
            ))
        inserted += 1
        occupied += 1

    if occupied:
        await db.flush()
    return occupied


# ── Assignment operations ────────────────────────────────────────────────


async def assign_personnel(
    project_id: str,
    personnel_id: str,
    role: str,
    requirement_desc: str,
    db: AsyncSession,
) -> ProjectPersonnel:
    """Assign a personnel record to a project with a specific role."""
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


async def unlink_qualification(
    project_id: str,
    requirement_name: str,
    resource_id: str,
    db: AsyncSession,
) -> bool:
    """解除某需求下指定资质链接（resource_id 为 qualification_id）."""
    rows = (await db.execute(
        select(ProjectQualification).where(
            ProjectQualification.project_id == project_id,
            ProjectQualification.requirement_name == requirement_name,
            ProjectQualification.qualification_id == resource_id,
        )
    )).scalars().all()
    for pq in rows:
        await db.delete(pq)
    await db.flush()
    return len(rows) > 0


async def unlink_contract(
    project_id: str,
    requirement_name: str,
    resource_id: str,
    db: AsyncSession,
) -> bool:
    """解除某业绩要求下指定合同链接（resource_id 为 contract_id）."""
    rows = (await db.execute(
        select(ProjectContract).where(
            ProjectContract.project_id == project_id,
            ProjectContract.requirement_name == requirement_name,
            ProjectContract.contract_id == resource_id,
        )
    )).scalars().all()
    for pc in rows:
        await db.delete(pc)
    await db.flush()
    return len(rows) > 0


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


async def link_contract(
    project_id: str,
    contract_id: str,
    requirement_name: str,
    db: AsyncSession,
) -> ProjectContract:
    """Link a historical contract to fulfil a performance-contract requirement."""
    pc = ProjectContract(
        project_id=project_id,
        contract_id=contract_id,
        requirement_name=requirement_name,
        match_status="matched",
    )
    db.add(pc)
    await db.flush()
    await db.refresh(pc)
    return pc


async def confirm_collection(project_id: str, db: AsyncSession) -> BidProject:
    """Mark collection as complete and advance the project to 'parsed'."""
    project = await db.get(BidProject, project_id)
    if not project:
        raise ValueError(f"Project {project_id} not found")

    # 批量自动占用确定性自动匹配（幂等：只为零落库的需求占用，用户可随后移除/替换）
    try:
        items = await analyze_collection_needs(project_id, db)
        rows = _pick_auto_occupy(items["document_items"], items["personnel_items"])
        if rows:
            occupied = await _auto_occupy_confident_matches(project_id, rows, db)
            logger.info("confirm_collection auto-occupied %d rows", occupied)
    except Exception as exc:
        logger.warning("Auto-occupy skipped: %s", exc)

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
        .options(
            selectinload(ProjectPersonnel.personnel).selectinload(
                Personnel.certificates
            )
        )
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
            "certificates": [
                {"cert_name": c.cert_name, "cert_number": c.cert_number}
                for c in (p.certificates or [])
            ] if p else [],
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

    # Contracts
    pc_result = await db.execute(
        select(ProjectContract)
        .where(ProjectContract.project_id == project_id)
        .options(selectinload(ProjectContract.contract))
    )
    contracts = []
    for pc in pc_result.scalars():
        c = pc.contract
        contracts.append({
            "id": pc.id,
            "contract_id": pc.contract_id,
            "requirement_name": pc.requirement_name,
            "project_name": c.project_name if c else "",
            "procurement_unit": c.procurement_unit if c else "",
            "contract_amount": c.contract_amount if c else "",
            "contract_date": str(c.contract_date) if c and c.contract_date else "",
            "source": "collected",
        })

    return {"qualifications": quals, "personnel": personnel, "company": company, "contracts": contracts}
