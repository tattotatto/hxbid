"""宏曦标书 - Edit Feedback Loop Engine.

Closes the learning loop: analyzes edit patterns across projects,
accumulates writing rules, and auto-upgrades patterns that appear
≥3 times into active prompt constraints for future generations.

Also handles bid-result-based weight adjustments for the vector store.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.edit_rule import EditRule
from app.models.project import BidProject, ProjectChapter
from app.services.vector_store import vector_store

logger = logging.getLogger(__name__)

UPGRADE_THRESHOLD = 3  # Same pattern ≥3 times → auto-upgrade to active


# ---------------------------------------------------------------------------
# Pattern key generation
# ---------------------------------------------------------------------------


def _make_pattern_key(edit_type: str, rule_text: str) -> str:
    """Generate a normalized dedup key from edit_type + rule text.

    Uses a hash prefix to keep keys short and consistent.
    """
    normalized = re.sub(r"\s+", "", rule_text)[:80]
    digest = hashlib.md5(f"{edit_type}:{normalized}".encode()).hexdigest()[:8]
    return f"{edit_type}:{digest}"


def _build_prompt_constraint(edit_type: str, rule_text: str) -> str:
    """Convert a rule into a prompt-ready constraint string."""
    type_labels = {
        "具体化不足": "每个段落必须包含至少1个具体事实（数字、日期、项目名、证书编号等）",
        "风格不对": "使用中文标书行业地道表达，避免口语化和模板化连接词",
        "信息错误": "涉及公司资质证书编号、人员身份证号等敏感信息必须在人工确认后才能导出",
        "冗余删除": "精简表达，避免凑篇幅的冗余描述",
        "补充遗漏": "对招标文件的每个要求必须作出针对性回应",
        "措辞优化": "句式结构多样化，相邻段落开头不能雷同",
    }
    prefix = type_labels.get(edit_type, f"写作规范（{edit_type}）")
    return f"{prefix}。新增规则：{rule_text}"


# ---------------------------------------------------------------------------
# Rule accumulation
# ---------------------------------------------------------------------------


async def accumulate_rules(
    edit_analysis_results: List[Dict[str, Any]],
    project_id: str,
    db: AsyncSession,
) -> Tuple[int, int]:
    """Accumulate rules from edit analysis results.

    For each extracted rule in the analysis:
    - If it matches an existing rule (by pattern_key), increment count.
    - If it's new, create a pending rule.
    - If count reaches UPGRADE_THRESHOLD, promote to active + generate prompt_constraint.

    Args:
        edit_analysis_results: List of edit analysis dicts from edit_analyzer.
        project_id: Source project ID for traceability.
        db: Async database session.

    Returns:
        (new_rules_count, upgraded_count)
    """
    new_count = 0
    upgraded_count = 0

    for analysis in edit_analysis_results:
        segments = analysis.get("segments", [])
        suggested_rules = analysis.get("suggested_rules", [])

        # Process explicitly suggested rules
        for rule_text in suggested_rules:
            if not rule_text:
                continue
            edit_type = "其他"
            for seg in segments:
                if seg.get("extracted_rule") == rule_text:
                    edit_type = seg.get("edit_type", "其他")
                    break

            pattern_key = _make_pattern_key(edit_type, rule_text)

            try:
                result = await db.execute(
                    select(EditRule).where(EditRule.pattern_key == pattern_key)
                )
                existing = result.scalar_one_or_none()

                if existing:
                    existing.occurrence_count += 1
                    # Append project ID
                    src_ids = json.loads(existing.source_project_ids)
                    if project_id not in src_ids:
                        src_ids.append(project_id)
                        existing.source_project_ids = json.dumps(src_ids)

                    # Check upgrade
                    if existing.occurrence_count >= UPGRADE_THRESHOLD and existing.status == "pending":
                        existing.status = "active"
                        existing.is_active = True
                        existing.prompt_constraint = _build_prompt_constraint(edit_type, rule_text)
                        upgraded_count += 1
                        logger.info(
                            "Rule upgraded to active: %s (count=%d)",
                            pattern_key, existing.occurrence_count,
                        )
                else:
                    new_rule = EditRule(
                        rule_text=rule_text,
                        edit_type=edit_type,
                        pattern_key=pattern_key,
                        occurrence_count=1,
                        source_project_ids=json.dumps([project_id]),
                    )
                    db.add(new_rule)
                    new_count += 1

                await db.flush()
            except Exception as exc:
                logger.warning("Failed to accumulate rule '%s': %s", pattern_key, exc)

        # Also process segment-level extracted rules
        for seg in segments:
            rule_text = seg.get("extracted_rule")
            if not rule_text or rule_text in suggested_rules:
                continue  # Already processed above

            edit_type = seg.get("edit_type", "其他")
            pattern_key = _make_pattern_key(edit_type, rule_text)

            try:
                result = await db.execute(
                    select(EditRule).where(EditRule.pattern_key == pattern_key)
                )
                existing = result.scalar_one_or_none()

                if existing:
                    existing.occurrence_count += 1
                    src_ids = json.loads(existing.source_project_ids)
                    if project_id not in src_ids:
                        src_ids.append(project_id)
                        existing.source_project_ids = json.dumps(src_ids)

                    if existing.occurrence_count >= UPGRADE_THRESHOLD and existing.status == "pending":
                        existing.status = "active"
                        existing.is_active = True
                        existing.prompt_constraint = _build_prompt_constraint(edit_type, rule_text)
                        upgraded_count += 1
                else:
                    new_rule = EditRule(
                        rule_text=rule_text,
                        edit_type=edit_type,
                        pattern_key=pattern_key,
                        occurrence_count=1,
                        source_project_ids=json.dumps([project_id]),
                    )
                    db.add(new_rule)
                    new_count += 1

                await db.flush()
            except Exception as exc:
                logger.warning("Failed to accumulate segment rule '%s': %s", pattern_key, exc)

    return new_count, upgraded_count


# ---------------------------------------------------------------------------
# Active rules query
# ---------------------------------------------------------------------------


async def get_active_prompt_constraints(db: AsyncSession) -> List[str]:
    """Return all active prompt constraints for injection into generation prompts.

    These are rules that have been observed ≥3 times across projects.
    """
    result = await db.execute(
        select(EditRule).where(EditRule.is_active == True)
    )
    active_rules = result.scalars().all()
    return [r.prompt_constraint for r in active_rules if r.prompt_constraint]


async def get_all_rules(db: AsyncSession) -> List[Dict[str, Any]]:
    """Return all rules (pending + active) for the frontend management view."""
    result = await db.execute(
        select(EditRule).order_by(EditRule.occurrence_count.desc())
    )
    rules = result.scalars().all()
    return [
        {
            "id": r.id,
            "rule_text": r.rule_text,
            "edit_type": r.edit_type,
            "pattern_key": r.pattern_key,
            "occurrence_count": r.occurrence_count,
            "status": r.status,
            "is_active": r.is_active,
            "prompt_constraint": r.prompt_constraint,
            "source_project_ids": json.loads(r.source_project_ids) if r.source_project_ids else [],
            "created_at": str(r.created_at),
        }
        for r in rules
    ]


# ---------------------------------------------------------------------------
# Bid result & weight adjustment
# ---------------------------------------------------------------------------


async def set_bid_result(
    project_id: str,
    result: str,  # "won" | "lost" | "pending"
    db: AsyncSession,
) -> Optional[BidProject]:
    """Mark a project's bid result and trigger weight adjustments.

    If 'won': re-index chapters with higher metadata weight for vector search.
    If 'lost': archive the project normally (no weight penalty).
    """
    project = await db.get(BidProject, project_id)
    if not project:
        return None

    project.bid_result = result
    if result == "won":
        project.status = "won"
    elif result == "lost":
        project.status = "lost"

    await db.flush()

    # Re-index with weight if won
    if result == "won" and vector_store.is_available():
        try:
            # Delete old entries first
            vector_store.delete_project(project_id)

            # Re-index with weight metadata
            chapters_result = await db.execute(
                select(ProjectChapter)
                .where(ProjectChapter.project_id == project_id)
                .order_by(ProjectChapter.order_index)
            )
            chapters = chapters_result.scalars().all()

            for ch in chapters:
                content = ch.final_content or ch.ai_generated_content
                if content:
                    vector_store.index_chapter(
                        chapter_id=ch.id,
                        project_id=project_id,
                        title=ch.title,
                        content=content,
                        metadata={"bid_result": "won", "weight": "2.0"},
                    )
            logger.info("Re-indexed won project %s with ×2.0 weight", project_id)
        except Exception as exc:
            logger.warning("Failed to re-index won project %s: %s", project_id, exc)

    return project


# ---------------------------------------------------------------------------
# Full feedback loop
# ---------------------------------------------------------------------------


async def run_feedback_loop(
    project_id: str,
    edit_analysis_results: List[Dict[str, Any]],
    bid_result: Optional[str] = None,
    db: AsyncSession = None,
) -> Dict[str, Any]:
    """Run the complete feedback loop for a finalized bid project.

    1. Accumulate edit rules from analysis results
    2. Upgrade rules that reached threshold
    3. If bid result provided, update project and adjust vector weights

    Returns summary dict suitable for API response.
    """
    summary = {
        "project_id": project_id,
        "rules_new": 0,
        "rules_upgraded": 0,
        "active_constraints": 0,
        "bid_result": bid_result,
        "vector_updated": False,
    }

    # 1+2: Rule accumulation
    if edit_analysis_results and db:
        try:
            new_count, upgraded_count = await accumulate_rules(
                edit_analysis_results, project_id, db
            )
            summary["rules_new"] = new_count
            summary["rules_upgraded"] = upgraded_count
            await db.commit()
        except Exception as exc:
            logger.warning("Rule accumulation failed: %s", exc)

    # Count current active constraints
    if db:
        try:
            active = await get_active_prompt_constraints(db)
            summary["active_constraints"] = len(active)
        except Exception:
            pass

    # 3: Bid result + weight
    if bid_result and db:
        try:
            await set_bid_result(project_id, bid_result, db)
            await db.commit()
            summary["vector_updated"] = (bid_result == "won")
        except Exception as exc:
            logger.warning("Bid result update failed: %s", exc)
            await db.rollback()

    return summary
