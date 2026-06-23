"""宏曦标书 - Fine-Tuning Dataset Exporter.

Exports AI-generated + human-edited chapter pairs from the feedback loop
as standard fine-tuning datasets (Alpaca, ShareGPT, ChatML formats) for
LoRA training on DeepSeek or other open-source models.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.edit_rule import EditRule
from app.models.project import BidProject, ProjectChapter
from app.services.anti_ai import analyze_chapter, report_to_dict

logger = logging.getLogger(__name__)

# System prompt used for training context
SYSTEM_PROMPT = """你是宏曦标书系统的AI助手，专注于为云南宏曦科技有限公司撰写保安/物业服务类投标文件。

写作规范：
1. 使用中文标书行业地道表达，避免模板化连接词
2. 每个段落必须包含至少1个具体事实（数字、日期、项目名、证书编号等）
3. 禁止使用空泛形容词和套话
4. 对招标文件的每个要求必须作出针对性回应，措辞不能照搬原文
5. 句式结构多样化，相邻段落开头不能雷同"""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class TrainingSample:
    """A single training sample: AI-generated input + human-edited expected output."""
    chapter_id: str
    chapter_title: str
    project_name: str
    bid_result: str
    input_text: str          # AI-generated (the "prompt" for training)
    output_text: str         # Human-edited (the "completion" for training)
    edit_score: float        # How much was edited (0-100; higher = more edited)
    quality_score: float     # Anti-AI trace score of the edited version
    created_at: str


# ---------------------------------------------------------------------------
# Dataset query
# ---------------------------------------------------------------------------


async def fetch_training_samples(
    db: AsyncSession,
    min_edit_chars: int = 50,
    max_samples: int = 500,
    bid_result_filter: Optional[str] = None,
) -> List[TrainingSample]:
    """Fetch chapter pairs suitable for fine-tuning.

    Only includes chapters where the human made substantial edits
    (≥ min_edit_chars difference between AI and final content).

    Args:
        db: Database session.
        min_edit_chars: Minimum character difference to include.
        max_samples: Max samples to return.
        bid_result_filter: "won", "lost", or None (all).

    Returns:
        List of TrainingSample ready for format conversion.
    """
    query = (
        select(ProjectChapter)
        .join(BidProject, ProjectChapter.project_id == BidProject.id)
        .where(
            ProjectChapter.ai_generated_content != "",
            ProjectChapter.final_content != "",
        )
    )

    if bid_result_filter:
        query = query.where(BidProject.bid_result == bid_result_filter)

    query = query.order_by(ProjectChapter.project_id).limit(max_samples)

    result = await db.execute(query)
    chapters = result.scalars().all()

    samples: List[TrainingSample] = []
    for ch in chapters:
        ai_content = ch.ai_generated_content
        final_content = ch.final_content

        if not ai_content or not final_content:
            continue

        # Calculate edit magnitude
        edit_chars = abs(len(final_content) - len(ai_content))
        if edit_chars < min_edit_chars:
            continue

        # Check content difference (skip identical content)
        from difflib import SequenceMatcher
        similarity = SequenceMatcher(None, ai_content, final_content).ratio()
        if similarity > 0.98:
            continue  # Too similar — no meaningful edit

        # Quality check on edited version
        report = analyze_chapter(ch.id, ch.title, final_content)
        quality_score = 100 - report.overall_score  # invert: higher = better

        # Get project name
        project = await db.get(BidProject, ch.project_id)
        project_name = project.name if project else ""
        bid_result = project.bid_result if project else ""

        samples.append(TrainingSample(
            chapter_id=ch.id,
            chapter_title=ch.title,
            project_name=project_name,
            bid_result=bid_result,
            input_text=ai_content,
            output_text=final_content,
            edit_score=round((1 - similarity) * 100, 1),
            quality_score=round(quality_score, 1),
            created_at=str(ch.project_id),  # proxy for time
        ))

    # Sort: won bids first, then by edit score
    samples.sort(key=lambda s: (
        0 if s.bid_result == "won" else 1,
        -s.edit_score,
    ))

    return samples[:max_samples]


# ---------------------------------------------------------------------------
# Format converters
# ---------------------------------------------------------------------------


def to_alpaca_format(samples: List[TrainingSample]) -> List[Dict[str, str]]:
    """Convert to Alpaca format: {instruction, input, output}."""
    return [
        {
            "instruction": SYSTEM_PROMPT,
            "input": f"章节：{s.chapter_title}\n项目：{s.project_name}\n请撰写此章节内容。",
            "output": s.output_text,
        }
        for s in samples
    ]


def to_sharegpt_format(samples: List[TrainingSample]) -> List[Dict[str, Any]]:
    """Convert to ShareGPT format: {conversations: [{from, value}]}."""
    return [
        {
            "conversations": [
                {"from": "system", "value": SYSTEM_PROMPT},
                {
                    "from": "human",
                    "value": f"请为项目「{s.project_name}」撰写「{s.chapter_title}」章节。",
                },
                {"from": "gpt", "value": s.output_text},
            ]
        }
        for s in samples
    ]


def to_chatml_format(samples: List[TrainingSample]) -> str:
    """Convert to ChatML format (plain text with <|im_start|> tokens)."""
    lines = []
    for s in samples:
        lines.append(f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>")
        lines.append(
            f"<|im_start|>user\n"
            f"请为项目「{s.project_name}」撰写「{s.chapter_title}」章节。"
            f"<|im_end|>"
        )
        lines.append(f"<|im_start|>assistant\n{s.output_text}<|im_end|>")
        lines.append("")  # separator
    return "\n".join(lines)


def to_jsonl_format(samples: List[TrainingSample]) -> str:
    """Convert to OpenAI JSONL format for fine-tuning API.

    Each line: {"messages": [{"role": "system", ...}, {"role": "user", ...}, {"role": "assistant", ...}]}
    """
    lines = []
    for s in samples:
        record = {
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"请为项目「{s.project_name}」撰写「{s.chapter_title}」章节。",
                },
                {"role": "assistant", "content": s.output_text},
            ]
        }
        lines.append(json.dumps(record, ensure_ascii=False))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Dataset statistics
# ---------------------------------------------------------------------------


async def get_dataset_stats(db: AsyncSession) -> Dict[str, Any]:
    """Return statistics about available training data."""
    # Total chapters with both AI and edited content
    total_result = await db.execute(
        select(func.count(ProjectChapter.id))
        .join(BidProject, ProjectChapter.project_id == BidProject.id)
        .where(
            ProjectChapter.ai_generated_content != "",
            ProjectChapter.final_content != "",
        )
    )
    total_edited = total_result.scalar() or 0

    # From won bids
    won_result = await db.execute(
        select(func.count(ProjectChapter.id))
        .join(BidProject, ProjectChapter.project_id == BidProject.id)
        .where(
            ProjectChapter.ai_generated_content != "",
            ProjectChapter.final_content != "",
            BidProject.bid_result == "won",
        )
    )
    won_edited = won_result.scalar() or 0

    # From lost bids
    lost_result = await db.execute(
        select(func.count(ProjectChapter.id))
        .join(BidProject, ProjectChapter.project_id == BidProject.id)
        .where(
            ProjectChapter.ai_generated_content != "",
            ProjectChapter.final_content != "",
            BidProject.bid_result == "lost",
        )
    )
    lost_edited = lost_result.scalar() or 0

    # Active rules count
    rules_result = await db.execute(
        select(func.count(EditRule.id)).where(EditRule.is_active == True)
    )
    active_rules = rules_result.scalar() or 0

    # Estimated training tokens (rough: 2 chars ≈ 1 token for Chinese)
    chars_result = await db.execute(
        select(func.sum(func.length(ProjectChapter.final_content)))
        .join(BidProject, ProjectChapter.project_id == BidProject.id)
        .where(
            ProjectChapter.ai_generated_content != "",
            ProjectChapter.final_content != "",
        )
    )
    total_chars = chars_result.scalar() or 0
    estimated_tokens = total_chars // 2

    return {
        "total_edited_chapters": total_edited,
        "won_chapters": won_edited,
        "lost_chapters": lost_edited,
        "active_rules": active_rules,
        "estimated_training_tokens": estimated_tokens,
        "ready_for_finetuning": total_edited >= 20,  # minimum viable dataset
    }
