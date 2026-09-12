"""宏曦标书 - 材料注入辅助函数.

将资料库里的材料（公司信息/资质证书/项目人员证书/历史合同）注入到对应章节。
无匹配章节时挂到最后一章作为 fallback，保证材料不丢失。

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ── 关键词匹配列表 ──
# 扩展自用户实际 PDF 章节标题（玉溪大红山矿业 PDF 第八节是「投标人基本资料」，
# 不在原 QUAL 关键词列表，导致 营业执照/法人身份证 不注入 → 用户反馈 bug）

QUAL_CHAPTER_KEYWORDS: list[str] = [
    "资格审查", "资格", "资质审查", "公司资质", "资质与业绩",
    "投标人基本资料", "投标人信息", "公司基本情况", "公司基本资料",
    "基本信息", "公司信息",
]

PERSONNEL_KEYWORDS: list[str] = [
    "人员", "配置", "团队", "组织", "人力", "管理架构", "岗位",
    "项目人员", "人员汇总",
]

CONTRACT_CHAPTER_KEYWORDS: list[str] = [
    "业绩", "类似项目", "项目经验", "成功案例",
    "投标人认为需要提供的其他", "其他内容", "其他材料",
    "业绩一览", "公司业绩",
]


def _has_content(text: str) -> bool:
    return bool(text and text.strip())


def _has_images(images: list[dict]) -> bool:
    return bool(images)


def inject_materials_into_chapters(
    chapters: list[dict],
    chapter_images: list[list[dict]],
    company_text_block: str,
    qual_text_block: str,
    all_qual_section_images: list[dict],
    personnel_cert_images: list[dict],
    contract_text_block: str,
    contract_images: list[dict],
) -> None:
    """将公司资料/资质证书/人员证书/历史合同注入到对应章节.

    匹配规则：
      - QUAL 关键词命中 → 公司信息 + 资质证书 文本 prepend，资质图片 inline
      - PERSONNEL 关键词命中 → 人员证书图片 inline
      - CONTRACT 关键词命中 → 合同文本 append + 合同图片 inline
    Fallback（任一未命中）：
      - 挂到最后一章，保证材料不丢失

    Args:
        chapters: ``[{"title": str, "content": str}, ...]``，就地修改 content。
        chapter_images: 与 chapters 等长的图片列表（每章一个），就地 extend。
        其余参数：来自资料库（CompanyProfile / Qualification / PersonnelCert / ProjectContract）。
    """
    if not chapters:
        return

    qual_payload_text = (company_text_block or "") + (qual_text_block or "")
    has_qual = _has_content(qual_payload_text) or _has_images(all_qual_section_images)
    has_personnel = _has_images(personnel_cert_images)
    has_contract = _has_content(contract_text_block) or _has_images(contract_images)

    matched_qual = False
    matched_personnel = False
    matched_contract = False

    for idx, ch in enumerate(chapters):
        title = ch.get("title", "")

        if has_qual and any(kw in title for kw in QUAL_CHAPTER_KEYWORDS):
            matched_qual = True
            if qual_payload_text:
                ch["content"] = qual_payload_text + "\n\n" + (ch.get("content") or "")
            if all_qual_section_images:
                chapter_images[idx].extend(all_qual_section_images)

        if has_personnel and any(kw in title for kw in PERSONNEL_KEYWORDS):
            matched_personnel = True
            chapter_images[idx].extend(personnel_cert_images)

        if has_contract and any(kw in title for kw in CONTRACT_CHAPTER_KEYWORDS):
            matched_contract = True
            if contract_text_block:
                ch["content"] = (ch.get("content") or "") + "\n" + contract_text_block
            if contract_images:
                chapter_images[idx].extend(contract_images)

    # ── Fallback：未命中时挂到最后一章 ──
    fallback_idx = len(chapters) - 1
    if fallback_idx < 0:
        return

    fallback_chapter = chapters[fallback_idx]

    if has_qual and not matched_qual:
        if qual_payload_text:
            fallback_chapter["content"] = (
                (fallback_chapter.get("content") or "") + "\n" + qual_payload_text
            )
        if all_qual_section_images:
            chapter_images[fallback_idx].extend(all_qual_section_images)
        logger.info(
            "QUAL materials fell back to last chapter: %s",
            fallback_chapter.get("title"),
        )

    if has_contract and not matched_contract:
        if contract_text_block:
            fallback_chapter["content"] = (
                (fallback_chapter.get("content") or "") + "\n" + contract_text_block
            )
        if contract_images:
            chapter_images[fallback_idx].extend(contract_images)
        logger.info(
            "CONTRACT materials fell back to last chapter: %s",
            fallback_chapter.get("title"),
        )

    # personnel 走 fallback 的语义：图片本应有人查看，挂到最后章节
    if has_personnel and not matched_personnel:
        chapter_images[fallback_idx].extend(personnel_cert_images)
        logger.info(
            "PERSONNEL cert images fell back to last chapter: %s",
            fallback_chapter.get("title"),
        )