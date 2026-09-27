"""宏曦标书 - 招标原文小节匹配器.

按用户输入的标题在招标文件原文中精确定位一个小节，供「目录确认页」实时
预览与生成阶段的原文回填共用。设计见
docs/superpowers/specs/2026-09-27-chapter-structure-editing-and-tender-matching-design.md

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

# 打分档位。0.75 以下不计候选；最高分 ≥ 0.9 且与次高分拉开 0.15 才算「确定命中」，
# 否则进「多候选」让用户挑。
MATCH_THRESHOLD = 0.75
STRONG_THRESHOLD = 0.9
AMBIGUITY_GAP = 0.15
MAX_CANDIDATES = 8

# 候选的**收集**门槛，低于 MATCH_THRESHOLD。定档仍按 MATCH_THRESHOLD ——
# 弱命中绝不当成 matched，但必须列出来让用户挑。
#
# 真实招标文件实测的必要性：用户写「投标保证金」而原文小节叫
# 「（五）投标保证金及基本户凭证」时，子串得分 0.8×5/13 ≈ 0.31 低于命中
# 阈值，于是状态是 missing 且**候选为空** —— 抽屉里写着「或从下方候选中选
# 一个」，下方却空无一物，用户只能拿到 AI 撰写的文本。对明确规定了格式的
# 章节，这就是废标风险。
SUGGEST_THRESHOLD = 0.2

# 标题行正则：**(层级, 模式)**，**顺序即优先级**——多级阿拉伯数字（1.1）必须先于
# 单级（1.）尝试，否则「1.1 项目概况」会被判成 level 3。同一位置只取首个命中。
_HEADER_PATTERNS: list[tuple[int, re.Pattern[str]]] = [
    (1, re.compile(
        r"^[ \t]*第\s*[一二三四五六七八九十百]{1,4}\s*[章节][ \t　]*([^\n]{1,80})$",
        re.MULTILINE,
    )),
    (1, re.compile(
        r"^[ \t]*[一二三四五六七八九十]{1,3}\s*[、．\.][ \t　]*([^\n]{1,80})$",
        re.MULTILINE,
    )),
    (2, re.compile(
        r"^[ \t]*[（(]\s*[一二三四五六七八九十]{1,3}\s*[）)][ \t　]*([^\n]{1,80})$",
        re.MULTILINE,
    )),
    (4, re.compile(
        r"^[ \t]*\d{1,2}(?:\.\d{1,2}){1,3}[ \t　]+([^\n]{1,80})$",
        re.MULTILINE,
    )),
    (3, re.compile(
        r"^[ \t]*\d{1,2}\s*[、．\.][ \t　]*([^\n]{1,80})$",
        re.MULTILINE,
    )),
]

# 全角 → 半角（数字、括号、句点、顿号、全角空格）
_FULLWIDTH_MAP = str.maketrans(
    "０１２３４５６７８９（）．、　",
    "0123456789().、 ",
)

# 标题末尾的括号注释（「投标承诺书（廉洁诚信承诺书）」里的后缀）——
# 归一化时剥掉。只认**末尾**的，中间带括号的标题不动。
_TRAILING_PAREN_RE = re.compile(r"[（(][^（()）]{0,80}[）)]\s*$")

# 整条标题就是一个括号（「（投标函）」）—— 脱括号而不是剥成空串
_WHOLE_PAREN_RE = re.compile(r"^\s*[（(]([^（()）]{1,80})[）)]\s*$")

# 标题行前的编号前缀，归一化时剥掉
_TITLE_NUM_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"第\s*[一二三四五六七八九十百]{1,4}\s*[章节]"
    r"|[一二三四五六七八九十]{1,3}\s*[、．\.]"
    r"|[（(]\s*[一二三四五六七八九十]{1,3}\s*[）)]"
    r"|\d{1,2}(?:\.\d{1,2}){0,3}\s*[、．\.]?"
    r")\s*"
)


@dataclass(frozen=True)
class SectionHeader:
    """一个标题行及其在语料中的位置."""

    title: str          # 标题文字（不含编号），已 strip
    raw: str            # 标题整行原文（含编号）
    start: int          # 标题行起始 offset
    content_start: int  # 标题行之后的 offset（正文起点候选）
    level: int          # 1=章/一、  2=（一）  3=1、  4=1.1


def normalize_title(s: str) -> str:
    """归一化标题：全角转半角、剥编号前缀、**剥末尾括号注释**、去空白、转小写.

    用户标题常从 Word 粘贴带全角空格与手写编号，招标原文的标题行也带编号，
    两边都剥掉才能等价比较。

    末尾的括号注释也要剥：AI 提取的章节标题常带注释后缀 ——
    「投标承诺书（廉洁诚信承诺书）」「法定代表人授权委托书（含法定代表人身份
    证明书）」—— 而招标原文的小节就叫「三、投标承诺书」。不剥时二者只是子串
    关系（0.8×5/13 ≈ 0.31），低于命中阈值 → 判 missing，用户看到假的「未匹配」。
    两侧都过这个函数，所以剥了是一致的。
    """
    if not s:
        return ""
    s = s.translate(_FULLWIDTH_MAP)
    s = _TITLE_NUM_PREFIX_RE.sub("", s)

    without_annotation = _TRAILING_PAREN_RE.sub("", s).strip()
    if without_annotation:
        s = without_annotation
    else:
        # 整条标题都在括号里（「(投标函)」）→ 脱括号，别剥成空串
        inner = _WHOLE_PAREN_RE.match(s)
        if inner:
            s = inner.group(1)

    return re.sub(r"\s+", "", s).strip().lower()


def enumerate_headers(text: str) -> list[SectionHeader]:
    """扫出全部标题行，按位置排序.

    同一位置只保留**首个命中**的层级——`_HEADER_PATTERNS` 已按"更具体优先"
    排好序，先到先得即可。
    """
    if not text:
        return []
    found: dict[int, SectionHeader] = {}
    for level, pattern in _HEADER_PATTERNS:
        for m in pattern.finditer(text):
            start = m.start()
            if start in found:
                continue
            found[start] = SectionHeader(
                title=m.group(1).strip(),
                raw=m.group(0).strip(),
                start=start,
                content_start=m.end(),
                level=level,
            )
    return [found[k] for k in sorted(found)]


# 独占一行的页码，如「-72-」「72」「— 72 —」。用于判断标题行后面到底有没有
# 正文——目录条目后面只剩页码。
_PAGE_NUMBER_LINE_RE = re.compile(r"^[\s\-—–]*\d+[\s\-—–]*$", re.MULTILINE)


@dataclass(frozen=True)
class MatchCandidate:
    """一个候选匹配结果."""

    title: str
    raw: str
    level: int
    start: int          # 标题行起始 offset
    end: int            # 本节结束 offset（下一个同级/更高级标题前）
    score: float
    page: int | None = None


def section_end(headers: list[SectionHeader], idx: int, text_len: int) -> int:
    """本节结束位置：下一个**层级 ≤ 本级**的标题行起点；没有则到语料末尾.

    这是对旧实现 ``extract_fixed_form_section`` 的实质修正——旧版碰到任何
    「一、」就停，遇到 `（一）` 这类子级标题反而不会截断，而遇到正确边界
    之外的同级标题又会截错。
    """
    cur_level = headers[idx].level
    for nxt in headers[idx + 1:]:
        if nxt.level <= cur_level:
            return nxt.start
    return text_len


def section_body(text: str, headers: list[SectionHeader], idx: int) -> str:
    """取第 idx 个标题行之后、到本节结束之间的正文（不含标题行本身）."""
    return text[headers[idx].content_start:section_end(headers, idx, len(text))]


def _bigrams(s: str) -> set[str]:
    if not s:
        return set()
    if len(s) < 2:
        return {s}
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _bigram_dice(a: str, b: str) -> float:
    ba, bb = _bigrams(a), _bigrams(b)
    if not ba or not bb:
        return 0.0
    return 2 * len(ba & bb) / (len(ba) + len(bb))


def score_title(target_norm: str, cand_norm: str) -> float:
    """按归一化标题打分：完全相等 1.0；互为子串 0.8×长度比；否则 bigram Dice.

    打分要**保守**——宁可判 ambiguous 让用户挑，也不要把不相干的章节当成命中，
    因为命中的后果是"照抄这段原文进标书"。
    """
    if not target_norm or not cand_norm:
        return 0.0
    if target_norm == cand_norm:
        return 1.0
    if target_norm in cand_norm or cand_norm in target_norm:
        shorter, longer = sorted((target_norm, cand_norm), key=len)
        return 0.8 * (len(shorter) / len(longer))
    return 0.6 * _bigram_dice(target_norm, cand_norm)


def has_body(text: str, headers: list[SectionHeader], idx: int) -> bool:
    """剔掉独占行的页码后是否还有正文（目录条目后面只剩页码）."""
    raw = section_body(text, headers, idx)
    return bool(_PAGE_NUMBER_LINE_RE.sub("", raw).strip())


def classify(scored: list[MatchCandidate]) -> str:
    """把打分结果定档为 matched / ambiguous.

    ``scored`` 必须已按分数降序排好。最高分 ≥ STRONG_THRESHOLD 且与次高分
    拉开 AMBIGUITY_GAP 才算确定命中；否则交给用户挑。
    """
    if not scored:
        return "missing"
    top = scored[0].score
    if top >= STRONG_THRESHOLD:
        runner = scored[1].score if len(scored) > 1 else 0.0
        if top - runner >= AMBIGUITY_GAP:
            return "matched"
    return "ambiguous"


@dataclass
class MatchResult:
    """一次匹配的完整结果."""

    status: str                                   # matched | ambiguous | missing | na
    source: str | None = None                     # format_section | full_text | table
    best: MatchCandidate | None = None
    candidates: list[MatchCandidate] = field(default_factory=list)
    table_index: int | None = None
    corpus_hash: str | None = None


def corpus_hash(text: str) -> str:
    """语料文本的 sha1，用于判断"原文变了没"."""
    return "sha1:" + hashlib.sha1((text or "").encode("utf-8")).hexdigest()


def page_at(page_map: list[dict] | None, offset: int) -> int | None:
    """按偏移量查真实页码；没有映射表返回 None（调用方不要把 None 编造成页号）."""
    if not page_map:
        return None
    page = None
    for entry in page_map:
        if entry.get("start", 0) <= offset:
            page = entry.get("page")
        else:
            break
    return page


def _pick_table_index(
    target_norm: str, tables: list[dict] | None, section_page: int | None,
) -> int | None:
    """选出该章节对应的表格下标.

    优先按页码就近取——招标原文里表格紧跟在小节标题之后；没有页码信息时退到
    用首行（表头）文字打分。
    """
    if not tables:
        return None

    if section_page is not None:
        later = [i for i, t in enumerate(tables) if (t.get("page") or 0) >= section_page]
        if later:
            return later[0]

    best_i, best_score = None, 0.0
    for i, t in enumerate(tables):
        rows = t.get("rows") or []
        if not rows:
            continue
        header_norm = normalize_title("".join(str(c or "") for c in rows[0]))
        s = score_title(target_norm, header_norm)
        if s > best_score:
            best_i, best_score = i, s
    return best_i if best_score >= MATCH_THRESHOLD else None


def _score_corpus(
    target_norm: str, text: str, page_map: list[dict] | None,
    threshold: float = MATCH_THRESHOLD,
) -> list[MatchCandidate]:
    """在单份语料里给所有标题行打分，返回 ≥ threshold 的候选（降序）."""
    headers = enumerate_headers(text)
    scored: list[MatchCandidate] = []
    for idx, h in enumerate(headers):
        s = score_title(target_norm, normalize_title(h.title))
        if s < threshold:
            continue
        if not has_body(text, headers, idx):
            continue
        scored.append(MatchCandidate(
            title=h.title,
            raw=h.raw,
            level=h.level,
            start=h.start,
            end=section_end(headers, idx, len(text)),
            score=s,
            page=page_at(page_map, h.start),
        ))
    scored.sort(key=lambda c: (-c.score, c.start))
    return scored


def match_tender_section(
    title: str,
    *,
    chapter_type: str = "fixed_form",
    format_section_text: str | None = None,
    full_text: str | None = None,
    format_tables: list[dict] | None = None,
    format_page_map: list[dict] | None = None,
) -> MatchResult:
    """按标题在招标原文中定位一个小节.

    语料优先级：``format_section_text``（投标文件格式章节）→ ``full_text``（全文）。
    ``chapter_type == "table"`` 时额外选出一张表并记 ``table_index``。

    注意：**不在此处做"匹配不上就降级 AI"的兜底**——那由调用方决定，
    本函数只如实报告 status，好让结构页能把红叉显示给用户。
    """
    target_norm = normalize_title(title)
    if not target_norm:
        return MatchResult(status="missing")

    if chapter_type == "ai_generated":
        return MatchResult(status="na")

    # 先按文字找小节（两种情况都要：文本类要靠它切片，表格类要靠它的页码定表）
    text_hit: MatchResult | None = None
    for source, text, pmap in (
        ("format_section", format_section_text, format_page_map),
        ("full_text", full_text, None),
    ):
        if not text:
            continue
        scored = _score_corpus(target_norm, text, pmap, SUGGEST_THRESHOLD)
        if not scored:
            continue
        strong = [c for c in scored if c.score >= MATCH_THRESHOLD]
        if strong:
            status = classify(strong)
            text_hit = MatchResult(
                status=status, source=source, best=strong[0],
                candidates=strong[:MAX_CANDIDATES] if status == "ambiguous" else [],
                corpus_hash=corpus_hash(text),
            )
        else:
            # 只有弱候选：不擅自当成命中（status 仍是 missing、best 为空），
            # 但把候选列出来让用户挑 —— 否则「或从下方候选中选一个」指向空列表
            text_hit = MatchResult(
                status="missing", source=source, best=None,
                candidates=scored[:MAX_CANDIDATES],
                corpus_hash=corpus_hash(text),
            )
        break

    if chapter_type != "table":
        return text_hit or MatchResult(status="missing")

    # 表格类：**那张表本身就是内容**，不要求标题文案先匹配上小节。
    # 有文字命中就拿它的页码就近取表；没有就直接用表头文字打分。
    section_page = text_hit.best.page if (text_hit and text_hit.best) else None
    table_index = _pick_table_index(target_norm, format_tables, section_page)
    if table_index is None:
        # 一张表都没有 → 退化成普通文本匹配的结果（可能 matched / ambiguous / missing）
        return text_hit or MatchResult(status="missing")

    return MatchResult(
        status="matched",
        source="table",
        best=text_hit.best if text_hit else None,
        candidates=[],
        table_index=table_index,
        corpus_hash=text_hit.corpus_hash if text_hit else None,
    )
