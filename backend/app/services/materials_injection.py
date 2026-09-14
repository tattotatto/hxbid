"""宏曦标书 - 材料注入辅助函数.

将资料库里的材料（公司信息/资质证书/项目人员证书/历史合同）注入到对应章节。
无匹配章节时挂到最后一章作为 fallback，保证材料不丢失。

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""
from __future__ import annotations

import logging
import re
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

# 业绩正题——「整块业绩表 + 全部合同扫描件」该落的地方。「其他材料」这类兜底章节
# 只在没有正题章节时才接收，否则同一份业绩表会在标书里出现三次（实测玉溪大红山：
# 商务文件其他材料 / 类似项目情况表 / 技术文件其他材料 三章各来一遍）。
CONTRACT_PRIMARY_KEYWORDS: list[str] = [
    "业绩", "类似项目", "项目经验", "成功案例",
]


def drop_already_embedded(images: list[dict], embedded_images: set[str]) -> list[dict]:
    """剔除「内容已经出过图」的材料，**并把留下来的就地登记进 embedded_images**.

    注意有副作用：返回的一定是新图（调用方据此出图就不会重复），登记之后同一份
    材料换个路径再来也会被后续调用丢掉——资质里那条与公司资料重复的营业执照就是
    这样被挡掉的，所以别把返回值再喂给第二次调用，也别绕过登记。

    按**内容**判重而不是按路径：同一份扫描件在库里常有两个存储路径（公司资料存
    ``uploads/company/x.png``，资质 OCR 又存了 ``ocr/y.png``），路径不同、字节
    相同。路径比较认不出，同一张营业执照就会在正文里出两次图。
    """
    from app.services.render_engine import image_content_key

    out = []
    for img in images:
        key = image_content_key(img["path"])
        if key in embedded_images:
            continue
        embedded_images.add(key)
        out.append(img)
    return out


def _has_content(text: str) -> bool:
    return bool(text and text.strip())


def _has_images(images: list[dict]) -> bool:
    return bool(images)


def _first_matching_index(chapters: list[dict], keywords: list[str]) -> int | None:
    """第一个标题命中关键词的章节下标；都没有则 None."""
    for idx, ch in enumerate(chapters):
        if any(kw in ch.get("title", "") for kw in keywords):
            return idx
    return None


# ── 法定代表人身份证扫描件：按招标原文的占位行就地插图 ──
# 招标文件自带「身份证正面扫描件」「身份证反面扫描件」这样的整行占位
# （玉溪大红山 PDF 第 1732-1733 行），那一行的位置就是图片该出现的地方。
# 「法定代表人授权委托书」既不命中下面任何关键词组，内容又是招标原文，
# 所以只能靠原文自己说明插图位置。
#
# 必须**整行**匹配：句子里的「附：身份证、职称证（如有）…等扫描件。」同样
# 含「身份证」+「扫描件」，但那是要项目负责人的证件，不是法人身份证的插图位；
# 整行匹配天然把它排除。「身份证号码：」同理不含扫描件/复印件字样，不触发。
_ID_SCAN_PLACEHOLDER_RE = re.compile(
    r"^(?:法定代表人|授权委托人|授权代理人|委托代理人|本人|投标人|附[:：])?"
    r"身份证(?:正面|反面|正反面)?"
    r"(?:复印件|扫描件|影印件)?"
    r"(?:粘贴处|复印件粘贴处|扫描件粘贴处)?$"
)
_ID_SCAN_STRIP_CHARS = "[]【】（）() \t　"


def _is_id_scan_placeholder(line: str) -> bool:
    """整行就是身份证扫描件占位标签（不是夹在句子里的提及）."""
    s = (line or "").strip().strip(_ID_SCAN_STRIP_CHARS)
    return bool(s) and bool(_ID_SCAN_PLACEHOLDER_RE.match(s))


def _build_id_scan_marker(scans: dict | None) -> str:
    """正反面都在 → 成对标记；只有一面 → 单张；都没有 → 空串（不动原文）."""
    scans = scans or {}
    front = str(scans.get("front_path") or "").strip()
    back = str(scans.get("back_path") or "").strip()
    front_label = str(scans.get("front_label") or "法定代表人身份证（正面）").strip()
    back_label = str(scans.get("back_label") or "法定代表人身份证（反面）").strip()
    if front and back:
        return f"[IDPAIR:{front}|{front_label}|{back}|{back_label}]"
    if front:
        return f"[IMG:{front}|{front_label}]"
    if back:
        return f"[IMG:{back}|{back_label}]"
    return ""


def _inject_id_card_scans(chapters: list[dict], scans: dict | None) -> None:
    """把身份证图标记插到占位行之后（最后一个占位行下面）."""
    marker = _build_id_scan_marker(scans)
    if not marker:
        return
    for ch in chapters:
        content = ch.get("content") or ""
        if not content:
            continue
        lines = content.split("\n")
        anchor = None
        for i, line in enumerate(lines):
            if _is_id_scan_placeholder(line):
                anchor = i
        if anchor is None:
            continue
        lines.insert(anchor + 1, marker)
        ch["content"] = "\n".join(lines)
        logger.info(
            "法定代表人身份证扫描件 injected after placeholder line in chapter: %s",
            ch.get("title"),
        )


def inject_materials_into_chapters(
    chapters: list[dict],
    chapter_images: list[list[dict]],
    company_text_block: str,
    qual_text_block: str,
    all_qual_section_images: list[dict],
    personnel_cert_images: list[dict],
    contract_text_block: str,
    legal_rep_id_card_scans: dict | None = None,
) -> None:
    """将公司资料/资质证书/人员证书/历史合同注入到对应章节.

    匹配规则：
      - QUAL 关键词命中 → 公司信息 + 资质证书 文本 prepend，资质图片 inline
      - PERSONNEL 关键词命中 → 人员证书图片 inline
      - CONTRACT：整块业绩表 append 到**一个**章节（业绩正题优先，其次兜底章节，
        都没有才挂最后一章）。合同扫描件以 [IMG:] 标记随文本一起进去，不再单独
        走 chapter_images——两条通道装的是同一批图，会每张出两次。
      - 法人身份证扫描件 → 插到章节里「身份证正/反面扫描件」占位行之后
      （内容驱动，不看章节标题——「法定代表人授权委托书」不命中任何关键词）
    Fallback（任一未命中）：
      - 挂到最后一章，保证材料不丢失

    Args:
        chapters: ``[{"title": str, "content": str}, ...]``，就地修改 content。
        chapter_images: 与 chapters 等长的图片列表（每章一个），就地 extend。
        legal_rep_id_card_scans: ``{"front_path", "front_label", "back_path",
            "back_label"}``（只有存在的面才给键），来自 company_profile。
        其余参数：来自资料库（CompanyProfile / Qualification / PersonnelCert / ProjectContract）。
    """
    if not chapters:
        return

    # 身份证扫描件走内容驱动，与下面的关键词匹配互不影响
    _inject_id_card_scans(chapters, legal_rep_id_card_scans)

    qual_payload_text = (company_text_block or "") + (qual_text_block or "")
    has_qual = _has_content(qual_payload_text) or _has_images(all_qual_section_images)
    has_personnel = _has_images(personnel_cert_images)
    has_contract = _has_content(contract_text_block)

    matched_qual = False
    matched_personnel = False

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

    if has_contract:
        target = _first_matching_index(chapters, CONTRACT_PRIMARY_KEYWORDS)
        if target is None:
            target = _first_matching_index(chapters, CONTRACT_CHAPTER_KEYWORDS)
        if target is None:
            target = fallback_idx
        chapters[target]["content"] = (
            (chapters[target].get("content") or "") + "\n" + contract_text_block
        )
        logger.info(
            "CONTRACT materials injected into chapter: %s",
            chapters[target].get("title"),
        )

    # personnel 走 fallback 的语义：图片本应有人查看，挂到最后章节
    if has_personnel and not matched_personnel:
        chapter_images[fallback_idx].extend(personnel_cert_images)
        logger.info(
            "PERSONNEL cert images fell back to last chapter: %s",
            fallback_chapter.get("title"),
        )