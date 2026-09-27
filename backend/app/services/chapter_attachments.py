"""宏曦标书 - 章节附件的清洗与落库.

附件清单有两个编辑入口（目录确认页的抽屉、信息搜集页的资质与证件），
两处必须写**同一份数据、同一套清洗口径** —— 所以这段从 api/chapters.py
挪到 service 层，供两边共用（service 不该反向 import api 模块）。

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

logger = logging.getLogger(__name__)


async def prune_attachments(tree: list, db: AsyncSession) -> list[str]:
    """剔除失效附件，返回被剔除的 label 列表.

    剔除三种情况：
      - 路径越出 ``UPLOAD_DIR``（防目录穿越）
      - 引用的资源库行已被删除
      - 与前面节点重复（按**内容 md5** 判重，与 materials_injection 的
        ``drop_already_embedded`` 同一口径）

    剔除而非整单拒绝：用户可能填了十个附件、其中一个被删了，整单报错会让
    他白填一遍。
    """
    from app.models.contract import Contract
    from app.models.personnel import PersonnelCertificate
    from app.models.qualification import Qualification
    from app.services.render_engine import image_content_key

    _MODEL_BY_KIND = {
        "qualification": Qualification,
        "personnel_cert": PersonnelCertificate,
        "contract": Contract,
    }

    upload_root = Path(settings.UPLOAD_DIR).resolve()
    pruned: list[str] = []
    seen_content: set[str] = set()

    async def _walk_async(nodes: list) -> None:
        for node in nodes or []:
            kept = []
            for att in node.get("attachments") or []:
                label = att.get("label") or att.get("id") or "(未命名)"
                kind = att.get("kind")
                path = att.get("path") or ""

                try:
                    resolved = (upload_root / path).resolve()
                    # is_relative_to 而不是 startswith：后者会把
                    # `…/uploads_evil/x` 这类同前缀的兄弟目录误判为通过
                    if not resolved.is_relative_to(upload_root):
                        pruned.append(label)
                        continue
                except (OSError, ValueError):
                    pruned.append(label)
                    continue

                if kind in _MODEL_BY_KIND:
                    model = _MODEL_BY_KIND[kind]
                    found = (
                        await db.execute(select(model.id).where(model.id == att.get("id")))
                    ).scalars().all()
                    if not found:
                        pruned.append(label)
                        continue

                key = image_content_key(path)
                if key in seen_content:
                    pruned.append(label)
                    continue
                seen_content.add(key)

                kept.append(att)
            node["attachments"] = kept
            await _walk_async(node.get("children") or [])

    await _walk_async(tree)
    return pruned


async def set_chapter_attachments(
    chapter, attachments: list, db: AsyncSession,
) -> tuple[list[dict], list[str]]:
    """替换某章节的附件清单，返回 ``(落库后的清单, 被剔除的 label 列表)``.

    信息搜集页的「资质与证件」区块挂/摘材料走这里；目录确认页的抽屉走
    ``PUT /bid/{pid}/chapter-structure`` 写整棵树。两个入口写的是**同一份数据**
    （`ProjectChapter.chapter_meta_json["attachments"]`，生成期只认它），
    清洗口径也同一套（``prune_attachments``），所以两边看到的结果一致。

    只改 ``attachments`` 一个键，`match` / `table_columns` 等原样保留。
    """
    try:
        meta = json.loads(getattr(chapter, "chapter_meta_json", None) or "{}")
    except (json.JSONDecodeError, TypeError):
        meta = {}
    if not isinstance(meta, dict):
        meta = {}

    node = {"attachments": list(attachments or [])}
    pruned = await prune_attachments([node], db)
    meta["attachments"] = node["attachments"]
    chapter.chapter_meta_json = json.dumps(meta, ensure_ascii=False)
    return node["attachments"], pruned
