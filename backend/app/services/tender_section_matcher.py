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
