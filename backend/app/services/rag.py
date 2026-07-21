"""宏曦标书 - RAG (Retrieval-Augmented Generation) Service.

Orchestrates multi-strategy context retrieval for AI chapter generation:
- Vector search for similar historical chapters (multi-query, relevance-filtered)
- Keyword-based qualification matching from the company resource library
- Tag-based personnel matching from the personnel database
- Context assembly with smart truncation and source tracking

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.personnel import Personnel
from app.models.qualification import Qualification
from app.models.company_profile import CompanyProfile
from app.services.vector_store import vector_store

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Max total chars for all retrieved similar chapter content combined
MAX_SIMILAR_CONTENT_CHARS = 32000  # was 8000 — increased for deep generation

# Max chars per single reference source (was 3000)
MAX_SOURCE_CHARS = 8000

# Cosine distance threshold — results above this are discarded
# (lower = more similar; 0.5–0.7 is a reasonable "maybe relevant" band)
SIMILARITY_THRESHOLD = 0.55

# Max results per query variant
MAX_RESULTS_PER_QUERY = 5

# Keywords commonly found in qualification requirements
QUAL_KEYWORDS = [
    "保安服务许可证", "营业执照", "质量管理", "ISO", "环境管理",
    "职业健康", "AAA", "信用", "安全生产", "保安员", "消防",
    "安检", "安防", "物业管理", "劳务派遣", "人力资源",
    "守押", "押运", "报警", "监控", "技防", "安全评估",
]

# Keywords commonly found in personnel requirements
PERSONNEL_KEYWORDS = [
    "保安师", "消防", "安检", "持证", "退伍", "军人",
    "大专", "本科", "管理", "队长", "经理", "主管",
    "门卫", "巡逻", "监控室", "应急", "处置", "急救",
    "工业园区", "大型活动", "商场", "写字楼", "小区",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_keywords(text: str, keyword_list: List[str]) -> List[str]:
    """Extract which keywords from *keyword_list* appear in *text*."""
    if not text:
        return []
    return [kw for kw in keyword_list if kw in text]


def _build_query_variants(chapter_title: str, requirements: dict) -> List[str]:
    """Build 2–3 query variants for multi-strategy vector retrieval.

    Variant 1: chapter title + requirements summary (broad context)
    Variant 2: chapter title only (focused on section semantics)
    Variant 3: requirements keywords only (when many are available)
    """
    variants = []

    # Variant 1: title + key requirement fields
    req_parts = []
    if requirements.get("service_requirements"):
        req_parts.append(" ".join(requirements["service_requirements"][:3]))
    if requirements.get("personnel_requirements"):
        req_parts.append(str(requirements["personnel_requirements"])[:200])
    if requirements.get("special_requirements"):
        req_parts.append(" ".join(requirements["special_requirements"][:3]))

    req_text = " ".join(req_parts)[:500]
    if req_text:
        variants.append(f"{chapter_title} {req_text}")
    else:
        variants.append(chapter_title)

    # Variant 2: chapter title only (different angle)
    if len(chapter_title) > 10:
        variants.append(f"标书章节：{chapter_title}")

    # Variant 3: requirements-focused when enough keywords exist
    if len(req_text) > 50:
        variants.append(f"招标要求：{req_text}")

    return variants


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def retrieve_similar_chapters(
    chapter_title: str,
    requirements: dict,
    project_id: str,
    n_results: int = 5,
) -> List[Dict[str, Any]]:
    """Multi-query vector retrieval for similar historical chapters.

    Runs 2–3 query variants against the vector store, merges results,
    removes duplicates, applies relevance threshold, and returns the
    top *n_results*.

    Args:
        chapter_title: The current chapter title.
        requirements: Parsed bid requirements dict.
        project_id: Current project ID (excluded from results to prevent
                    self-retrieval).
        n_results: Max final results to return.

    Returns:
        List of {title, content, distance, metadata} dicts, sorted by relevance.
    """
    if not vector_store.is_available():
        return []

    queries = _build_query_variants(chapter_title, requirements)
    seen_ids: set = set()
    all_results: List[Dict[str, Any]] = []

    for query in queries:
        try:
            raw = vector_store.search_similar(
                query=query,
                n_results=n_results,
                filter_project_id=project_id,
            )
            for r in raw:
                chunk_key = r["metadata"].get("chapter_id", "") + str(r.get("distance", ""))
                if chunk_key not in seen_ids:
                    seen_ids.add(chunk_key)
                    # Apply relevance threshold
                    distance = r.get("distance")
                    if distance is not None and distance > SIMILARITY_THRESHOLD:
                        continue
                    all_results.append(r)
        except Exception as exc:
            logger.warning("Vector search failed for query '%s...': %s", query[:50], exc)
            continue

    # Sort by distance (lower = more similar) and take top N
    all_results.sort(key=lambda r: r.get("distance", 1.0))
    top = all_results[:n_results]

    # Format for consumption by generate_chapter_with_materials
    return [
        {
            "title": r["metadata"].get("title", ""),
            "content": r["content"],
            "distance": r.get("distance"),
            "source_chapter_id": r["metadata"].get("chapter_id", ""),
            "source_project_id": r["metadata"].get("project_id", ""),
        }
        for r in top
    ]


async def match_qualifications(
    requirements: dict,
    db: AsyncSession,
    max_results: int = 8,
) -> List[Dict[str, Any]]:
    """Match company qualifications to bid requirements.

    Strategy:
    1. Extract qualification-related keywords from requirements text.
    2. Search the qualifications table for matching names and keywords.
    3. If no keyword matches, return all qualifications (better to have
       all available than none).

    Args:
        requirements: Parsed bid requirements dict.
        db: Async database session.
        max_results: Max qualifications to return.

    Returns:
        List of dicts with name, cert_number, issuing_authority, notes.
    """
    requirements_text = (
        str(requirements.get("qualification_requirements", []))
        + " "
        + str(requirements.get("special_requirements", []))
        + " "
        + str(requirements.get("service_requirements", []))
    )

    matched_keywords = _extract_keywords(requirements_text, QUAL_KEYWORDS)

    try:
        if matched_keywords:
            # Build LIKE conditions for each matched keyword
            conditions = []
            for kw in matched_keywords:
                conditions.append(Qualification.name.ilike(f"%{kw}%"))
                conditions.append(Qualification.notes.ilike(f"%{kw}%"))

            stmt = (
                select(Qualification)
                .where(or_(*conditions))
                .limit(max_results)
            )
        else:
            # No specific keywords — return all qualifications
            stmt = select(Qualification).limit(max_results)

        result = await db.execute(stmt)
        quals = result.scalars().all()

        return [
            {
                "name": q.name,
                "cert_number": q.cert_number,
                "issuing_authority": q.issuing_authority,
                "notes": q.notes,
            }
            for q in quals
        ]
    except Exception as exc:
        logger.warning("Qualification matching failed: %s", exc)
        return []


async def match_personnel(
    requirements: dict,
    db: AsyncSession,
    max_results: int = 10,
) -> List[Dict[str, Any]]:
    """Match company personnel to bid requirements.

    Strategy:
    1. Extract personnel-related keywords from requirements.
    2. Search personnel by tags ILIKE for each keyword.
    3. Eager-load experiences and certificates for rich context.
    4. Fall back to returning all active personnel if no keywords match.

    Args:
        requirements: Parsed bid requirements dict.
        db: Async database session.
        max_results: Max personnel to return.

    Returns:
        List of dicts with name, education, tags, experiences, certificates.
    """
    requirements_text = (
        str(requirements.get("personnel_requirements", ""))
        + " "
        + str(requirements.get("service_requirements", []))
    )

    matched_keywords = _extract_keywords(requirements_text, PERSONNEL_KEYWORDS)

    try:
        if matched_keywords:
            conditions = []
            for kw in matched_keywords:
                conditions.append(Personnel.tags.ilike(f"%{kw}%"))

            stmt = (
                select(Personnel)
                .where(or_(*conditions))
                .options(
                    selectinload(Personnel.experiences),
                    selectinload(Personnel.certificates),
                )
                .limit(max_results)
            )
        else:
            # Return all personnel with their experiences and certificates
            stmt = (
                select(Personnel)
                .options(
                    selectinload(Personnel.experiences),
                    selectinload(Personnel.certificates),
                )
                .limit(max_results)
            )

        result = await db.execute(stmt)
        personnel_list = result.scalars().all()

        formatted = []
        for p in personnel_list:
            experiences = []
            for exp in (p.experiences or []):
                experiences.append({
                    "organization": exp.organization,
                    "position": exp.position,
                    "project_scale": exp.project_scale,
                    "responsibilities": exp.responsibilities,
                    "achievements": exp.achievements,
                })

            certificates = []
            for cert in (p.certificates or []):
                certificates.append({
                    "cert_name": cert.cert_name,
                    "cert_number": cert.cert_number,
                })

            formatted.append({
                "name": p.name,
                "id_card": p.id_card,
                "education": p.education,
                "tags": p.tags,
                "experiences": experiences,
                "certificates": certificates,
            })

        return formatted
    except Exception as exc:
        logger.warning("Personnel matching failed: %s", exc)
        return []


async def assemble_chapter_context(
    chapter_title: str,
    requirements: dict,
    project_id: str,
    db: AsyncSession,
) -> Tuple[List[Dict], List[Dict], List[Dict], Dict[str, Any]]:
    """Master RAG function — retrieve all context for one chapter.

    Combines vector search, qualification matching, personnel matching,
    and company profile into a single call. Returns the matched data plus
    a source summary for the frontend.

    Args:
        chapter_title: Current chapter title.
        requirements: Parsed bid requirements dict.
        project_id: Current project ID (for exclusion filter).
        db: Async database session.

    Returns:
        Tuple of (similar_chapters, matched_qualifications, matched_personnel, source_summary)
        where source_summary = {
            "chapter_id": str,
            "similar_count": int,
            "qual_count": int,
            "personnel_count": int,
            "has_company": bool,
            "similar_titles": [str, ...],
        }
    """
    similar_chapters: List[Dict] = []
    matched_quals: List[Dict] = []
    matched_personnel: List[Dict] = []
    company: dict | None = None

    # 1. Vector search for similar chapters
    try:
        similar_chapters = await retrieve_similar_chapters(
            chapter_title=chapter_title,
            requirements=requirements,
            project_id=project_id,
        )
    except Exception as exc:
        logger.warning("RAG similar chapter retrieval failed: %s", exc)

    # 2. Qualification matching
    try:
        matched_quals = await match_qualifications(requirements, db)
    except Exception as exc:
        logger.warning("RAG qualification matching failed: %s", exc)

    # 3. Personnel matching
    try:
        matched_personnel = await match_personnel(requirements, db)
    except Exception as exc:
        logger.warning("RAG personnel matching failed: %s", exc)

    # 4. Company profile
    try:
        cp_result = await db.execute(select(CompanyProfile).limit(1))
        cp = cp_result.scalar_one_or_none()
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
    except Exception as exc:
        logger.warning("RAG company profile fetch failed: %s", exc)

    # 5. Build source summary for the frontend
    source_summary = {
        "similar_count": len(similar_chapters),
        "qual_count": len(matched_quals),
        "personnel_count": len(matched_personnel),
        "has_company": company is not None,
        "similar_titles": [
            ch.get("title", "") for ch in similar_chapters[:5]
        ],
        "company": company,
    }

    # 5. Smart truncation — keep total similar content under MAX chars
    if similar_chapters:
        total_chars = 0
        for ch in similar_chapters:
            total_chars += len(ch["content"])
        if total_chars > MAX_SIMILAR_CONTENT_CHARS:
            # Distribute budget proportionally
            budget_per = MAX_SIMILAR_CONTENT_CHARS // len(similar_chapters)
            for ch in similar_chapters:
                ch["content"] = ch["content"][:budget_per]

    return similar_chapters, matched_quals, matched_personnel, source_summary
