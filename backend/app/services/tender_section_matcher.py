"""宏曦标书 - 招标原文小节匹配器.

按用户输入的标题在招标文件原文中精确定位一个小节，供「目录确认页」实时
预览与生成阶段的原文回填共用。设计见
docs/superpowers/specs/2026-09-27-chapter-structure-editing-and-tender-matching-design.md

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 打分档位。0.75 以下不计候选；最高分 ≥ 0.9 且与次高分拉开 0.15 才算「确定命中」，
# 否则进「多候选」让用户挑。
MATCH_THRESHOLD = 0.75
STRONG_THRESHOLD = 0.9
AMBIGUITY_GAP = 0.15
MAX_CANDIDATES = 8

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
    """归一化标题：全角转半角、剥编号前缀、去所有空白、转小写.

    用户标题常从 Word 粘贴带全角空格与手写编号，招标原文的标题行也带编号，
    两边都剥掉才能等价比较。
    """
    if not s:
        return ""
    s = s.translate(_FULLWIDTH_MAP)
    s = _TITLE_NUM_PREFIX_RE.sub("", s)
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
