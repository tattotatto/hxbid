"""宏曦标书 - 格式提取器 — 智能格式章节定位.
"""
import logging
import re

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 文档结构搜索常量
# ---------------------------------------------------------------------------
_TOC_SCAN_LIMIT = 2000       # 目录关键词搜索范围（文档前 N 字符）
_TOC_WINDOW_SIZE = 5000      # 目录区域扫描窗口大小
_TOC_BODY_SEEK = 2000        # 从目录起向后偏移，定位正文起点
_SECTION_MAX_CHARS = 12000   # 章节文本最大提取长度
_NEXT_CHAPTER_GAP = 100      # 从章节头向后跳过，搜索下一章

# 文档长度阈值
_MIN_DOC_CHARS = 500         # 最小文档长度（短于此值拒绝处理）
_TAIL_MIN_CHARS = 1000       # 尾部兜底最小长度
_TAIL_MAX_CHARS = 10000      # 尾部兜底最大返回长度
_TAIL_START_FRAC = 0.6       # 尾部起始比例（后 40%）

# ---------------------------------------------------------------------------
# 编译正则
# ---------------------------------------------------------------------------

# 格式章节关键词匹配模式（按优先级排列）
FORMAT_SECTION_PATTERNS = [
    re.compile(r'第[一二三四五六七八九十\d]+章\s*.*?投标文件格式'),
    re.compile(r'第[一二三四五六七八九十\d]+章\s*.*?投标书格式'),
    re.compile(r'第[一二三四五六七八九十\d]+章\s*.*?投标文件.*格式'),
    re.compile(r'第[一二三四五六七八九十\d]+节\s*.*?投标文件格式'),
    re.compile(r'第[一二三四五六七八九十\d]+部分\s*.*?投标文件格式'),
]

# 通用章节头切分模式 (MULTILINE 下 ^ 即可匹配行首)
CHAPTER_HEADER_PATTERN = re.compile(
    r'^(第[一二三四五六七八九十\d]+[章节篇部分][\s　]*(?:.*?))(?=\n|$)',
    re.MULTILINE,
)


def _find_by_keyword(text: str) -> dict | None:
    """通过关键词匹配定位'投标文件格式'章节."""
    for pattern in FORMAT_SECTION_PATTERNS:
        match = pattern.search(text)
        if match:
            start = match.start()
            chapter_title = match.group(0).strip()
            chapter_number_match = re.search(r'第([一二三四五六七八九十\d]+)', chapter_title)
            chapter_number = chapter_number_match.group(1) if chapter_number_match else None

            # 找到下一章的起始位置作为结束边界
            next_chapter_match = CHAPTER_HEADER_PATTERN.search(text, match.end())
            end = next_chapter_match.start() if next_chapter_match else min(len(text), start + _SECTION_MAX_CHARS)

            section_text = text[start:end]
            return {
                "chapter_number": chapter_number,
                "chapter_title": chapter_title,
                "section_text": section_text,
                "method": "keyword_match",
            }
    return None


def _find_by_toc(text: str) -> dict | None:
    """通过目录推断'投标文件格式'章节位置."""
    # 找到目录区域
    toc_match = re.search(r'目\s*录|目\s*次', text[:_TOC_SCAN_LIMIT])
    if not toc_match:
        return None

    # 从目录区域开始搜索格式章节引用
    toc_end = toc_match.start() + _TOC_WINDOW_SIZE
    toc_area = text[toc_match.start():min(toc_end, len(text))]

    # 在目录中搜索格式章节标题
    for pattern in FORMAT_SECTION_PATTERNS:
        m = pattern.search(toc_area)
        if m:
            chapter_title = m.group(0).strip()
            # 找到了目录中的引用，直接跳到正文中对应位置
            # 在目录之后搜索相同的章节标题
            body_start = toc_match.start() + _TOC_BODY_SEEK
            body_match = re.search(
                re.escape(chapter_title[:30]) + r'.*',
                text[body_start:],
            )
            if body_match:
                abs_start = body_start + body_match.start()
                next_ch = CHAPTER_HEADER_PATTERN.search(text, abs_start + _NEXT_CHAPTER_GAP)
                end = next_ch.start() if next_ch else min(len(text), abs_start + _SECTION_MAX_CHARS)
                return {
                    "chapter_number": None,
                    "chapter_title": chapter_title,
                    "section_text": text[abs_start:end],
                    "method": "toc_inference",
                }
    return None


def _split_by_chapter_headers(text: str) -> list[dict]:
    """按章节头将全文切分为章节块."""
    matches = list(CHAPTER_HEADER_PATTERN.finditer(text))
    if not matches:
        return []

    chapters = []
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chapters.append({
            "title": title,
            "text": text[start:end],
            "start": start,
        })
    return chapters


def _find_by_semantic_chunks(chapters: list[dict]) -> dict | None:
    """通过语义分块定位：对每个章节块评分，找到最可能是格式章节的块."""
    # 关键词打分
    format_keywords = ["投标文件格式", "格式要求", "投标文件组成", "投标文件内容",
                       "投标函", "开标一览表", "法定代表人", "授权委托书",
                       "承诺书", "签章", "盖章", "目录要求", "装订"]
    best_chapter = None
    best_score = 0
    for ch in chapters:
        score = sum(1 for kw in format_keywords if kw in ch["text"])
        if score > best_score:
            best_score = score
            best_chapter = ch
    if best_chapter and best_score >= 3:
        return {
            "chapter_number": None,
            "chapter_title": best_chapter["title"],
            "section_text": best_chapter["text"],
            "method": "semantic_chunking",
        }
    return None


def _fallback_tail(text: str) -> dict | None:
    """兜底：取文档后40%作为格式章节候选."""
    tail_start = int(len(text) * _TAIL_START_FRAC)
    tail_text = text[tail_start:]
    if len(tail_text) > _TAIL_MIN_CHARS:
        return {
            "chapter_number": None,
            "chapter_title": "投标文件格式（自动定位）",
            "section_text": tail_text[:_TAIL_MAX_CHARS],
            "method": "tail_fallback",
        }
    return None


async def locate_format_section(full_text: str) -> dict | None:
    """智能定位招标文件中的'投标文件格式'章节.

    四级定位策略（按优先级）：
    1. 关键词匹配 — 直接搜索"第X章...投标文件格式"
    2. 目录推断 — 从目录页定位格式章节在正文中的位置
    3. 语义分块 — 按章节头切分后关键词打分
    4. 尾部兜底 — 取文档后40%

    Returns:
        dict with keys: chapter_number, chapter_title, section_text, method
        None if all methods fail
    """
    if not full_text or len(full_text) < _MIN_DOC_CHARS:
        return None

    # Strategy 1: keyword match
    result = _find_by_keyword(full_text)
    if result:
        logger.info("Format section located via keyword match: %s", result["chapter_title"])
        return result

    # Strategy 2: TOC inference
    result = _find_by_toc(full_text)
    if result:
        logger.info("Format section located via TOC inference")
        return result

    # Strategy 3: semantic chunking
    chapters = _split_by_chapter_headers(full_text)
    if chapters:
        result = _find_by_semantic_chunks(chapters)
        if result:
            logger.info("Format section located via semantic chunking (score-based)")
            return result

    # Strategy 4: tail fallback
    result = _fallback_tail(full_text)
    if result:
        logger.warning("Format section located via tail fallback — low confidence")
        return result

    logger.warning("Could not locate format section in document")
    return None
