"""宏曦标书 - PDF格式章节完整提取器.

使用 pdfplumber 从招标文件 PDF 中提取格式章节的完整文本和表格数据。
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pdfplumber

logger = logging.getLogger(__name__)

# 格式章节定位关键词（按优先级）
FORMAT_KEYWORDS = [
    "投标文件格式",
    "投标书格式",
    "投标文件组成",
    "第六章 投标文件格式",
    "第五章 投标书格式",
    "第四章 投标文件格式",
]

# 评标办法定位关键词（用户已确认清单，运行时仍可能扩展）
EVALUATION_KEYWORDS = [
    "评标办法",
    "评分办法",
    "综合评分法",
    "评分标准",
    "评审办法",
    "评审标准",
]

# 评标办法章节标题行（独占一行才算锚点）：
# - `$` 锚定制把目录行（「第四章 评标办法….41」带页码）排除在锚之外；
# - `^` 锚定制把正文交叉引用（「评标委员会按照第四章“评标办法”规定…」）排除。
# 2026-08-28 修复：真实招标文件目录页误锚导致「未检测到评标办法」（抓回须知全文）。
EVALUATION_HEADING_RE = re.compile(
    r"^(?:第[一二三四五六七八九十\d]+[章部]\s*)?"
    r"(评标办法|评分办法|评审办法|评分标准|评审标准|综合评分法)"
    r"(?:\s*[（(][^（()）]{0,30}[)）])?\s*$"
)

# 兄弟章题（定位「招标文件由下列部分组成」章节名称清单的后随行）
_CHAPTER_SIBLING_RE = re.compile(r"^第[一二三四五六七八九十\d]+[章部]\s*\S")

# 完整章标题行（用于「下一章」边界判定）：`第X章` + 简短标题。
# 尾段不含空白/引号/句读——「第四章“评标办法”规定的方法进行评审。」这类
# 交叉引用正文行因此被排除。
_CHAPTER_HEADING_RE = re.compile(
    r"^第[一二三四五六七八九十百零\d]+[章部]\s*[^\s“”\"'。；，,;、]{2,24}$"
)


def _leading_lines(text: str, n: int = 3) -> List[str]:
    """页面顶部的前 n 个非空行.

    章节边界只在页首判定——整页子串匹配会把正文表格里的零星提及
    （如初步评审表第 10 条的「投标文件至少包括投标函…」）误当成章节标题。
    """
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()][:n]


def _is_next_chapter_heading(line: str) -> bool:
    """页首的「下一章」标题行.

    评标办法自身的章题（`第四章 评标办法`）常作为页眉在每页页首重复，
    不算边界。
    """
    return bool(_CHAPTER_HEADING_RE.match(line)) and not EVALUATION_HEADING_RE.match(line)


def locate_format_pages(pdf) -> Tuple[int, int] | None:
    """定位招标文件中"投标文件格式"章节的起止页码.

    从文档末尾向前搜索关键词以快速定位格式章节（格式章节通常位于文档后半部分）。

    Returns:
        (start_page, end_page) 0-indexed, or None if not found.
        start_page 是章节首页（如"第六章 投标文件格式"所在页）。
        end_page 是文档末尾（格式章节通常包含到文档结束）。
    """
    # 从后向前搜索，格式章节通常位于文档末尾
    num_pages = len(pdf.pages)
    for i in range(num_pages - 1, -1, -1):
        text = pdf.pages[i].extract_text() or ""
        for kw in FORMAT_KEYWORDS:
            if kw in text:
                # 再向前回溯找到章节的真正起始页（关键词可能在标题行之后）
                start_page = i
                # 向前最多回溯 3 页，找更靠前的匹配。
                # 注意 range 的 stop 是开区间：要覆盖 i-1/i-2/i-3，stop 必须写
                # i-4。旧实现写 max(i - 3, -1)，实际只回溯了 2 页——当真正
                # 的章首页正好在 i-3（如大红山：p76 投标函正文提及关键词，
                # 章首在 p73，中间隔着 p74 目录、p75 封面两页无关键词）时
                # 够不到，start_page 停在提及页，章名页/目录/封面整页被排除。
                if i > 0:
                    for j in range(i - 1, max(i - 4, -1), -1):
                        prev_text = pdf.pages[j].extract_text() or ""
                        for pk in FORMAT_KEYWORDS:
                            if pk in prev_text:
                                start_page = j
                                break
                        else:
                            continue
                        break

                end_page = num_pages - 1
                logger.info(
                    "Format section: pages %d-%d (keyword: '%s', total pages: %d)",
                    start_page, end_page, kw, num_pages,
                )
                return start_page, end_page
    return None


def locate_evaluation_section(pdf):
    """定位招标文件中"评标办法/评分标准"章节的起止页码并提取全文.

    评标办法通常在文中部，parse_bid_requirements 只解析前 15,000 字符经常截断，
    这里直接按页扫描 PDF 全文，绕开截断（克隆 locate_format_pages 模式）。

    Returns:
        (start_page, end_page, text)，0-indexed；未定位返回 None。
        end_page 止于「投标文件格式」/「投标函」章节之前（评标办法通常在其后截止）。
    """
    num_pages = len(pdf.pages)
    start_page = None
    # 章节标题行锚定优先。三重判别：
    #  1) `$`/`^` 锚剔掉目录行（带页码）与正文交叉引用（句中引用）；
    #  2) 章节起始页标题必然位于页首 → 仅认前 ~3 个非空行；
    #  3) 章节名称罗列清单（「招标文件由下列部分组成：第一章…」）的标题后
    #     紧跟兄弟章题 → 排除（真实章节起始页标题后跟章节内容）。
    for i in range(num_pages):
        text = pdf.pages[i].extract_text() or ""
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        for idx, line in enumerate(lines):
            if not EVALUATION_HEADING_RE.match(line):
                continue
            if idx > 2:
                break  # 页首无标题行的页面不是章节起始页
            if any(_CHAPTER_SIBLING_RE.match(s) for s in lines[idx + 1:idx + 3]):
                break  # 后随兄弟章题 → 章节名称罗列清单
            start_page = i
            break
        if start_page is not None:
            break
    if start_page is None:
        # 兜底：无章节标题行的罕见文档 → 原关键词扫描，跳过前 10% 封面/目录区
        skip = max(0, num_pages // 10)
        logger.info(
            "No evaluation heading line found; falling back to keyword scan "
            "from page %d of %d", skip, num_pages,
        )
        for i in range(skip, num_pages):
            text = pdf.pages[i].extract_text() or ""
            if any(kw in text for kw in EVALUATION_KEYWORDS):
                start_page = i
                break
    if start_page is None:
        logger.info("No evaluation section found in %d pages", num_pages)
        return None

    end_page = num_pages - 1
    for i in range(start_page + 1, num_pages):
        text = pdf.pages[i].extract_text() or ""
        lead = _leading_lines(text)

        # 格式章节标题 / 「投标函」标题出现在页首 → 评标办法结束。
        # 「投标函」只认行首：格式章节里它是标题行，而正文表格中的顺带
        # 提及（初步评审表第 10 条）不是章节边界。
        if any(any(kw in ln for kw in FORMAT_KEYWORDS) for ln in lead):
            end_page = i - 1
            break
        if any(ln.startswith("投标函") for ln in lead):
            end_page = i - 1
            break

        # 下一个「第X章」标题出现在页首 → 评标办法结束。
        # 旧实现只认 FORMAT_KEYWORDS，遇到「第五章 合同条款及格式」这类
        # 与格式无关的下一章标题不认边界，会把后续整份文档吞进评标办法。
        if lead and _is_next_chapter_heading(lead[0]):
            end_page = i - 1
            break

    section_text = extract_text_from_pages(pdf, start_page, end_page)
    if not section_text.strip():
        logger.info("Evaluation section located but empty (pages %d-%d)", start_page, end_page)
        return None
    logger.info(
        "Evaluation section: pages %d-%d (%d chars)",
        start_page, end_page, len(section_text),
    )
    return start_page, end_page, section_text


# 页码行：整行只有一个数字，可带 -、—、第…页 之类的装饰。
_PAGE_NUMBER_RE = re.compile(r"^[\s\-—–－_]*\d{1,4}[\s\-—–－_]*$")

# 页码只可能落在页眉/页脚，按页高比例判定。**不能只看「整行只有数字」**——
# 开标一览表里的序号、金额都可能独占一行，删掉就是丢数据。
_PAGE_NUMBER_MARGIN_RATIO = 0.10


def _is_page_number(text: str, top: float, bottom: float, height: float) -> bool:
    """这行是不是印刷页码（页眉/页脚里的孤立数字）."""
    if not height or not _PAGE_NUMBER_RE.match(text):
        return False
    margin = height * _PAGE_NUMBER_MARGIN_RATIO
    return bottom > height - margin or top < margin


def extract_text_from_pages(pdf, start: int, end: int) -> str:
    """提取指定页码范围的所有文本.

    只做一件事：**丢掉页眉/页脚的印刷页码**。页码在招标文件里是正文流里独立
    的一行，原样转储会跟着页面信息一起进章节正文（实测「-78-」夹在「投标人
    名称：」和「日期：」之间）。

    行结构原样保留（不拼排版换行）——``locate_evaluation_section`` 那类按行
    锚定的地方依赖逐行结构，要拼段落的用 ``extract_clean_text_from_pages``。

    Args:
        pdf: pdfplumber.PDF 实例
        start: 起始页（0-indexed）
        end: 结束页（0-indexed，包含）

    Returns:
        合并后的全部文本
    """
    parts = []
    for i in range(start, end + 1):
        if i >= len(pdf.pages):
            break
        page = pdf.pages[i]
        lines = getattr(page, "extract_text_lines", None)
        lines = lines() if callable(lines) else None
        if not lines:
            # 无行几何（个别页/旧版本 pdfplumber）→ 退回原样转储
            text = page.extract_text()
            if text:
                parts.append(text)
            continue
        height = getattr(page, "height", 0) or 0
        kept = [
            str(ln.get("text") or "").strip()
            for ln in lines
            if not _is_page_number(
                str(ln.get("text") or "").strip(),
                ln.get("top") or 0.0,
                ln.get("bottom") or 0.0,
                height,
            )
        ]
        text = "\n".join(s for s in kept if s)
        if text:
            parts.append(text)
    return "\n\n".join(parts)

# 段内换行的行距上限（相对字号）。实测大红山第 75/76 页：段内换行 9.95–10.34，
# 换段/换列表项/换表单字段 19.91–20.03，中间没有灰区。取 1.4 倍字号。
_WRAP_GAP_RATIO = 1.4

# 「上一行顶到了右边距」的容差按该行自身高度取，即一个字宽。
# 排版换行发生在「下一个字放不下」时，所以：
#   - 两端对齐的正文（投标函那种）会拉伸填满，实测差 0.1–0.7pt；
#   - 左对齐的（开标一览表的「标段名称：…」）就只能空着，实测差 7.3pt。
# 用固定的小容差会把后者漏掉。一个字宽的余量对短标题仍然安全——第 83 页
# 「八、投标人基本资料」x1=234，离栏边还有 270pt。
_FLUSH_TOLERANCE_CHARS = 1.0


def _join_wrapped(prev: str, cur: str) -> str:
    """把被排版拆开的两行拼回去.

    西文换行处原先就有一个空格（不可能在词中间断），拼回去要补上，
    否则 ``con`` + ``tract`` 粘成一个词；中文换行是纯视觉的，不能插空格。
    """
    if prev and cur and prev[-1].isascii() and prev[-1].isalnum() \
            and cur[0].isascii() and cur[0].isalnum():
        return prev + " " + cur
    return prev + cur


# 列表项标号：`a)` `1）` `（3）` `一、` `• ` 等。
# 行距判不出「换列表项」——大红山第 78 页整页统一 10.31pt，和新起一段的
# 段内换行一模一样，纯几何判定会把 `a) …` 吃进上一行，而同一页 `b)`–`i)`
# 因为上一行不满行又是独立行。标号是内容上的硬信号，补在这里。
# 只在标号后面**必须**跟标号标点时才算，免得把「…金额为」+「100000元」这种
# 数字开头的真续行一起挡掉。
_LIST_MARKER_RE = re.compile(
    r"^(?:"
    r"[A-Za-z][)）.、]"
    r"|\d{1,3}[)）.、]"
    r"|[（(]\s*(?:\d{1,3}|[一二三四五六七八九十]{1,3})\s*[)）]"
    r"|[一二三四五六七八九十]{1,3}[、．.]"
    r"|[•·◆▪●○*]\s"
    r")"
)


def _starts_list_item(text: str) -> bool:
    """该行是不是列表项的开头（而不是上一行的续行）."""
    return bool(_LIST_MARKER_RE.match(text))


def _in_table(line: dict, table_boxes: List[tuple]) -> bool:
    """该行是否落在某个表格区域内.（按整行都在框内判定，压线的不算.）"""
    top, bottom = line.get("top") or 0.0, line.get("bottom") or 0.0
    return any(box[1] <= top and bottom <= box[3] for box in table_boxes)


def _clean_page_lines(page, lines: List[dict], table_boxes: List[tuple],
                      right_edge: float) -> List[str]:
    """单页的清洗后行列表：丢页码行，把段内换行的残句拼成整行.

    Args:
        lines: ``page.extract_text_lines()`` 的结果
        table_boxes: ``page.find_tables()`` 的 bbox 列表
        right_edge: 正文栏右边界（文档级，见 ``extract_clean_text_from_pages``）
    """
    if not lines:
        return []

    height = getattr(page, "height", 0) or 0

    out: List[str] = []
    prev_bottom = prev_height = None
    prev_flush = prev_in_table = False
    for ln in lines:
        text = str(ln.get("text") or "").strip()
        if not text:
            continue
        top = ln.get("top") or 0.0
        bottom = ln.get("bottom") or 0.0
        # 页眉/页脚里的孤立数字 = 页码（招标文件把页码排进了正文流，不删就会
        # 跟着固定格式章节一起进标书）
        if _is_page_number(text, top, bottom, height):
            continue
        height_pt = bottom - top
        flush = (ln.get("x1") or 0.0) >= right_edge - height_pt * _FLUSH_TOLERANCE_CHARS
        now_in_table = _in_table(ln, table_boxes)
        # 上一行顶到右边距（被排版换行截断的）+ 行距是段内的 + 两行都不在表格
        # 里，才是续行。不要求当前行也顶到右边距：三段以上的段落，中间的续行
        # 自己也是满的。当前行是列表项开头时一律不拼——行距分不出来。
        if (out and prev_bottom is not None and prev_flush and not prev_in_table
                and not now_in_table and not _starts_list_item(text)):
            gap = top - prev_bottom
            if 0 <= gap < _WRAP_GAP_RATIO * min(prev_height, height_pt):
                out[-1] = _join_wrapped(out[-1], text)
                prev_bottom, prev_height, prev_flush = bottom, height_pt, flush
                prev_in_table = now_in_table
                continue
        out.append(text)
        prev_bottom, prev_height, prev_flush = bottom, height_pt, flush
        prev_in_table = now_in_table
    return out


def extract_clean_text_from_pages(pdf, start: int, end: int) -> str:
    """提取指定页码范围文本，按行几何还原被排版拆开的段落.

    与 ``extract_text_from_pages`` 的区别：后者是 ``page.extract_text()`` 的
    原样转储，每个视觉行都是一行——招标文件里两端对齐的 CJK 正文会在右边距
    处硬换行，印刷页码也混在正文流里。这两样直接喂给固定格式章节，标书正文
    里就会出现断在词中间的残句和「-75-」。

    页与页之间保持硬换行（跨页多半是换表单行/换章节，拼上去反而错）。
    ``extract_text_from_pages`` 保持不变——它的输出还供评标办法定位等使用，
    那些地方依赖逐行结构。

    Args:
        pdf: pdfplumber.PDF 实例
        start: 起始页（0-indexed）
        end: 结束页（0-indexed，包含）

    Returns:
        合并后的文本，页间以空行分隔
    """
    pages = []
    for i in range(start, end + 1):
        if i >= len(pdf.pages):
            break
        page = pdf.pages[i]
        lines = page.extract_text_lines()
        if not lines:
            continue
        pages.append((page, lines, [t.bbox for t in page.find_tables()]))

    # 正文栏右边界取整份文档的表格外行，**不能在单页里取**：第 83 页表格占了大
    # 半页，表格外只剩「八、投标人基本资料」「（一）投标人基本情况表」两行标题，
    # 页内取最大值就把它俩当成了右边界，于是标题被判成续行拼成一句。
    # 表格比正文栏宽，算进来又会把边界抬高，所以只取表格外的行。
    edge_candidates = [
        (ln.get("x1") or 0.0)
        for _, lines, boxes in pages
        for ln in lines
        if not _in_table(ln, boxes)
    ] or [
        (ln.get("x1") or 0.0) for _, lines, _ in pages for ln in lines
    ]
    right_edge = max(edge_candidates) if edge_candidates else 0.0

    parts = []
    for page, lines, boxes in pages:
        cleaned = _clean_page_lines(page, lines, boxes, right_edge)
        if cleaned:
            parts.append("\n".join(cleaned))
    return "\n\n".join(parts)


def extract_tables_from_pages(pdf, start: int, end: int) -> list[dict]:
    """提取指定页码范围的所有表格.

    Args:
        pdf: pdfplumber.PDF 实例
        start: 起始页（0-indexed）
        end: 结束页（0-indexed，包含）

    Returns:
        list of {"page": int, "table_index": int, "rows": list[list[str | None]]}
    """
    tables = []
    for i in range(start, end + 1):
        if i >= len(pdf.pages):
            break
        page_tables = pdf.pages[i].extract_tables()
        if not page_tables:
            continue
        for j, rows in enumerate(page_tables):
            if rows and len(rows) >= 2:  # 过滤空表和单行"表"
                tables.append({
                    "page": i + 1,  # 1-indexed
                    "table_index": j,
                    "rows": rows,
                })
    return tables


def extract_format_section(pdf_path: str) -> dict:
    """完整提取招标文件格式章节.

    提取流程：
    1. 打开 PDF
    2. 定位"投标文件格式"章节起止页
    3. 提取全部文本（含封面、目录、正文）
    4. 提取所有表格

    Args:
        pdf_path: PDF 文件路径（支持 Path-like 和字符串）

    Returns:
        {
            "full_text": str,          # 格式章节全部文本
            "tables": list[dict],      # 表格数据
            "start_page": int,         # 起始页（1-indexed）
            "end_page": int,           # 结束页（1-indexed）
            "total_pages": int,        # 文档总页数
        }

    Raises:
        FileNotFoundError: PDF 文件不存在
        ValueError: PDF 文件无法解析
    """
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    pdf = pdfplumber.open(str(path))

    try:
        location = locate_format_pages(pdf)
        if not location:
            logger.warning("Format section not found, using full document")
            start, end = 0, len(pdf.pages) - 1
        else:
            start, end = location

        # 固定格式章节要按招标原文逐字回填，必须用清洗过的文本：原样转储会把
        # 排版硬换行和印刷页码一起带进标书正文（实测「其中：治」/「安保卫业务」
        # 断在词中间、「-75-」夹在「投标人名称：」和「日期：」之间）。
        full_text = extract_clean_text_from_pages(pdf, start, end)
        tables = extract_tables_from_pages(pdf, start, end)

        result = {
            "full_text": full_text,
            "tables": tables,
            "start_page": start + 1,
            "end_page": end + 1,
            "total_pages": len(pdf.pages),
        }

        logger.info(
            "Extracted format section: %d chars text, %d tables from pages %d-%d (total %d pages)",
            len(full_text), len(tables), result["start_page"], result["end_page"], result["total_pages"],
        )

        return result
    finally:
        pdf.close()


def extract_full_document(pdf_path: str) -> dict:
    """提取整个 PDF 文档的文本和表格.

    当不需要定位特定章节时使用此函数。

    Args:
        pdf_path: PDF 文件路径

    Returns:
        {"full_text": str, "tables": list[dict], "total_pages": int}
    """
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    pdf = pdfplumber.open(str(path))

    try:
        full_text = extract_text_from_pages(pdf, 0, len(pdf.pages) - 1)
        tables = extract_tables_from_pages(pdf, 0, len(pdf.pages) - 1)

        return {
            "full_text": full_text,
            "tables": tables,
            "total_pages": len(pdf.pages),
        }
    finally:
        pdf.close()
