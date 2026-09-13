"""宏曦标书 - 模板填充引擎.

完整提取的格式章节全文 → AI 标注变量位置 → 批量替换 → 生成填充后的文档内容.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import re
import json
import logging
from datetime import date
from typing import Any, Dict, List

from app.services.ai_adapter import AIEmptyContentError

logger = logging.getLogger(__name__)

SCAN_SYSTEM_PROMPT = """你是投标文件分析专家。招标文件的格式章节已完整提取。
你的任务是扫描全文，找出所有需要投标人**填写**的位置，标注变量名。

变量名只能从以下列表选取：
- company_name: 投标人公司名称
- legal_rep_name: 法定代表人姓名
- legal_rep_id_number: 法定代表人身份证号
- business_license_number: 统一社会信用代码/营业执照号
- address: 公司地址
- contact_phone: 联系电话
- website: 公司网站
- contact_person: 联系人
- fax: 传真
- zip_code: 邮编
- registered_capital: 注册资金
- account_number: 开户银行账号
- bank_name: 开户银行
- project_name: 招标项目名称
- tenderer_name: 招标人名称（致函对象）
- tenderer_agency_name: 招标代理机构
- tender_number: 招标编号
- bid_total_amount: 投标总报价（含税/不含税）
- bid_total_amount_words: 投标报价大写
- bid_unit_amount: 投标单价
- bid_deposit_amount: 投标保证金金额
- service_period: 服务期限
- service_location: 服务地点
- date: 日期
- bid_validity_days: 投标有效期天数

识别填写位置的规则（按出现频次）：
1. **下划线占位符**：`致：________` 中 `________` → 替换为对应变量值
2. **标签词占位符**：`致： 招标人名称` 中 `招标人名称` 是占位符词，应替换为 tenderer_name
   - `日期： 年 月 日` 中三个空白处都应替换为 date
   - `项目名称：` 后面跟的空白 → project_name
3. **括号内空白**：`（招标编号为 ）` 括号内的空白 → tender_number

如果你不确定某个位置该对应哪个变量，用 unknown_1, unknown_2 等标记，并在 warnings 中说明。

表格中每个空单元格如果已有标签行标明该填什么，标注对应的变量名。

返回 JSON:
{
  "text_replacements": [
    {"original": "________", "var": "company_name", "context_before": "投标人名称："},
    {"original": "招标人名称", "var": "tenderer_name", "context_before": "致："},
    {"original": "投标人名称：", "var": null, "note": "这是标签，不替换"},
  ],
  "table_fills": [
    {"page": 68, "table_index": 1, "row": 0, "col": 1, "var": "company_name"}
  ],
  "warnings": ["第X页'xxx'处不确定对应哪个变量"]
}
"""


async def scan_and_mark_variables(
    full_text: str,
    tables: list[dict],
    ai_adapter,
    known_values: dict | None = None,
) -> dict:
    """AI 扫描全文，标注所有变量位置.

    Args:
        known_values: 已从公司资料/招标文件解析出的真实值（变量名 → 值）。
            作为提示注入，避免模型对已知信息（招标人名称、项目名称、
            招标编号等）凭空猜测或标成 unknown。为空则不注入该段。
    """
    tables_json = json.dumps(tables[:10], ensure_ascii=False)  # 限制表格数量
    hint_block = ""
    if known_values:
        hint_block = (
            "\n已知变量值（这些是真实值，直接采用，不要另行编造）：\n"
            "---\n"
            f"{json.dumps(known_values, ensure_ascii=False, indent=2)}\n"
            "---\n"
        )
    prompt = f"""请扫描以下招标文件格式章节，找出所有需要投标人填写的位置。

全文（含封面、目录、正文）：
---
{full_text[:15000]}
---

表格数据：
---
{tables_json}
---
{hint_block}
请标注每个填写位置对应的变量名。直接返回JSON，不要其他文字。"""

    try:
        response = await ai_adapter.chat_completion(
            messages=[
                {"role": "system", "content": SCAN_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            # 预算必须覆盖「推理 + 正文」。推理模型（deepseek-flash 系）的
            # reasoning_tokens 与正文共用这一份预算：大红山实测同一请求在
            # 8192 下 reasoning=8192 吃满、正文 0 字节（finish_reason=length），
            # 扫描永远拿不到标注，固定格式章节整章回退 AI 自由生成；
            # 给到 32768 时 reasoning=12129、正文 4354 字正常收尾。
            max_tokens=32768,
            response_format={"type": "json_object"},
        )
    except AIEmptyContentError as exc:
        # 预算问题，不是数据问题：推理把 max_tokens 吃光、正文为空。
        # 必须与「AI 没话说」区分开——旧实现把两者一锅端成一句含糊的
        # 「AI扫描失败」，于是「调高 max_tokens」这条正解被埋掉，
        # 固定格式章节整章静默回退 AI 自由生成（大红山潜伏至今的成因）。
        logger.error(
            "Variable scanning ABORTED: token budget exhausted by reasoning, "
            "raise max_tokens. %s", exc,
        )
        return {
            "text_replacements": [],
            "table_fills": [],
            "warnings": [f"AI token 预算被推理吃满（需调高 max_tokens）: {exc}"],
        }
    except Exception as exc:
        logger.error(
            "Variable scanning failed (%s): %s", type(exc).__name__, exc,
            exc_info=True,
        )
        return {
            "text_replacements": [],
            "table_fills": [],
            "warnings": [f"AI扫描失败({type(exc).__name__}): {exc}"],
        }

    try:
        result = json.loads(response)
    except json.JSONDecodeError as exc:
        logger.error("Variable scanning returned invalid JSON: %s", exc)
        return {
            "text_replacements": [],
            "table_fills": [],
            "warnings": [f"AI返回非法JSON: {exc}"],
        }

    return result


# ---------------------------------------------------------------------------
# 人民币金额大写（元角分）
# ---------------------------------------------------------------------------

_CN_DIGITS = "零壹贰叁肆伍陆柒捌玖"
_CN_UNITS = ["", "拾", "佰", "仟"]
_CN_BIG_UNITS = ["", "万", "亿", "万亿"]

_AMOUNT_RE = re.compile(r"\d+(?:\.\d+)?")


def _int_to_chinese(num: int) -> str:
    """整数部分 → 中文大写（不含「元」）."""
    if num == 0:
        return "零"
    groups: list[int] = []
    while num > 0:
        groups.append(num % 10000)
        num //= 10000
    groups.reverse()  # 高位在前
    out = ""
    for idx, group in enumerate(groups):
        if group == 0:
            continue  # 整组为零，由下一非零组补「零」
        seg = ""
        zero_pending = False
        for j in range(3, -1, -1):
            digit = (group // 10 ** j) % 10
            if digit == 0:
                zero_pending = True
                continue
            if zero_pending and seg:
                seg += "零"
            zero_pending = False
            seg += _CN_DIGITS[digit] + _CN_UNITS[j]
        # 组内高位为零且前面已有内容 → 补「零」（10001 → 壹万零壹）
        if out and group < 1000:
            out += "零"
        out += seg + _CN_BIG_UNITS[len(groups) - 1 - idx]
    return out


def amount_to_chinese_words(text: str) -> str:
    """从任意金额文本中提取数字并转成人民币大写（元角分）.

    容忍「￥1,234.00」「120000元」等写法；解析不出数字（空值/「面议」/
    「[待补充：X]」占位符）时返回空字符串，由调用方决定回退占位。
    """
    if not text:
        return ""
    cleaned = str(text).replace(",", "").replace("，", "")
    match = _AMOUNT_RE.search(cleaned)
    if not match:
        return ""
    int_part, _, dec_part = match.group(0).partition(".")
    int_part = int_part.lstrip("0") or "0"
    if len(int_part) > 16:  # 超出常规投标金额量级，放弃以免给出错值
        return ""
    dec_part = (dec_part + "00")[:2]
    yuan = _int_to_chinese(int(int_part))
    jiao, fen = int(dec_part[0]), int(dec_part[1])
    if jiao == 0 and fen == 0:
        return f"{yuan}元整"
    out = f"{yuan}元"
    if jiao:
        out += _CN_DIGITS[jiao] + "角"
    elif fen:
        out += "零"
    if fen:
        out += _CN_DIGITS[fen] + "分"
    return out


def build_variable_values(
    company_profile: dict | None = None,
    requirements: dict | None = None,
) -> dict:
    """构建变量值映射.

    招标编号/报价/期限/地点/保证金复用 ``extract_bid_opening_data`` 的取数约定
    （与开标一览表同源），避免同一份数据两处解析出不同结果。

    取不到的值一律落**空字符串**，由 ``batch_fill_text`` 解释为「这个槽位不动」，
    招标原文的空白/占位词原样留在标书里，用户一眼能看到该填哪儿。

    空串不是随便选的：曾经这里填 ``[待补充：X]`` 可见占位，结果被当成真值写进
    正文——``post_scan`` 只扫 ``{word}``，扫不到 ``[word]``，没有任何兜底拦得住，
    成品会带着「[待补充：投标总报价]」交付。裸 ``[var]`` 同理（模型自造的
    unknown_N 会从 ``variables.get(var, f"[{var}]")`` 那个默认值漏出来）。
    """
    company = company_profile or {}
    reqs = requirements or {}
    opening = extract_bid_opening_data(requirements=reqs, company_profile=company)

    def _s(value: Any) -> str:
        return str(value or "").strip()

    total_amount = _s(opening.get("total_price"))

    return {
        "company_name": _s(company.get("company_name")),
        "legal_rep_name": _s(company.get("legal_rep_name")),
        "business_license_number": _s(company.get("business_license_number")),
        "address": _s(company.get("address")),
        "contact_phone": _s(company.get("contact_phone")),
        "website": _s(company.get("website")),
        "contact_person": _s(company.get("contact_person")),
        "fax": _s(company.get("fax")),
        "zip_code": _s(company.get("zip_code")),
        "registered_capital": _s(company.get("registered_capital")),
        "account_number": _s(company.get("account_number")),
        "bank_name": _s(company.get("bank_name")),
        "project_name": _s(reqs.get("project_name")),
        "tenderer_name": _s(reqs.get("tenderer_name")),
        "legal_rep_id_number": _s(company.get("legal_rep_id_number")),
        "tenderer_agency_name": _s(reqs.get("tenderer_agency_name")),
        "tender_number": _s(opening.get("tender_number")),
        "bid_total_amount": total_amount,
        "bid_total_amount_words": _s(amount_to_chinese_words(total_amount)),
        "bid_unit_amount": _s(opening.get("unit_price")),
        "bid_deposit_amount": _s(opening.get("bid_deposit_amount")),
        "service_period": _s(opening.get("service_period")),
        "service_location": _s(opening.get("service_location")),
        "date": date.today().strftime("%Y年%m月%d日"),
        "bid_validity_days": "120",
    }


_LABEL_TAIL_RE = re.compile(r"[:：]\s*$")  # 标签型原文，如「投标人：」
_INLINE_WS_RE = re.compile(r"[ \t　]*")  # 同行空白（不含换行）


def _anchor_end(text: str, anchor: str) -> int | None:
    """锚点在正文中结束的位置；无锚点或找不到时返回 None."""
    if not anchor:
        return None
    pos = text.find(anchor)
    return None if pos < 0 else pos + len(anchor)


def _locate(text: str, anchor: str, needle: str) -> int:
    """needle 的位置：优先取锚点之后的首次出现，否则退回全文首次."""
    start = _anchor_end(text, anchor)
    if start is not None:
        idx = text.find(needle, start)
        if idx >= 0:
            return idx
    return text.find(needle)


def batch_fill_text(text: str, text_replacements: list[dict]) -> str:
    """批量文本替换：将原文中的空白/占位符替换为实际值.

    按 replacement 的长度降序排列，避免短串先替换破坏长串。

    定位靠模型给的 ``context_before``（"这个空前面是什么"），不用
    ``str.replace`` 的全文首次匹配——同一份格式章节里「致： 招标人名称」和
    「投标总报价为： 万元」都是空格/占位词，首次匹配会把值填进无关的位置
    （大红山实测：金额被插到了「致：」后面，而该填的那格仍空着）。

    原文分三类，处理方式不同：
      * 标签型（``投标人：``）——保留标签，值接在冒号后，不能把标签吃掉；
      * 空/纯空白——模型只给了位置，按锚点填入并吃掉紧跟的同行空白；
      * 占位词（``招标人名称``/``________``）——在锚点范围内替换掉它。

    **没有值的槽位一律跳过**：拿不到数据时把占位词/空白原样留着，用户一眼能看出
    该填哪儿；写空串等于把招标原文里的「（出具保函银行名称）」这类词删掉，正文就
    残了。裸 ``[var]`` 占位同样不行（``post_scan`` 扫不到 ``[word]``，拦不住）。
    """
    # 按 original 长度降序
    sorted_reps = sorted(
        text_replacements,
        key=lambda r: len(r.get("original", "")),
        reverse=True,
    )

    result = text
    for rep in sorted_reps:
        var = rep.get("var")
        if not var:
            continue
        value = str(rep.get("value") or "").strip()
        if not value:
            continue  # 取不到值：原文原样留着，绝不覆盖成空串/裸占位
        original = rep.get("original") or ""
        anchor = rep.get("context_before") or ""

        if not original.strip():
            start = _anchor_end(result, anchor)
            if start is None:
                continue  # 无锚点无从定位，只能跳过
            tail = _INLINE_WS_RE.match(result, start)
            result = result[:start] + value + result[tail.end():]
            continue

        idx = _locate(result, anchor, original)
        if idx < 0:
            continue
        # 标签型保留标签本身，只在其后追加值
        replacement = original + value if _LABEL_TAIL_RE.search(original) else value
        result = result[:idx] + replacement + result[idx + len(original):]

    return result


def batch_fill_tables(tables: list[dict], table_fills: list[dict], variables: dict) -> list[dict]:
    """批量表格填充：在指定位置填入变量值.

    与 ``batch_fill_text`` 同一条规矩：取不到值的槽位原样留着，绝不写 ``[var]``
    占位——单元格里的 ``[unknown_1]`` 会一路走到成品标书里。
    """
    result = [{"page": t["page"], "table_index": t["table_index"], "rows": [list(row) for row in t["rows"]]} for t in tables]

    for fill in table_fills:
        page = fill.get("page")
        ti = fill.get("table_index")
        row = fill.get("row")
        col = fill.get("col")
        var = fill.get("var", "")
        value = str(variables.get(var) or "").strip()
        if not value:
            continue

        for t in result:
            if t["page"] == page and t["table_index"] == ti:
                if row < len(t["rows"]) and col < len(t["rows"][row]):
                    t["rows"][row][col] = value

    return result


def post_scan(text: str) -> list[str]:
    """后处理兜底：扫描残留的空白/占位符."""
    issues = []
    # 扫描残留的空白下划线
    blanks = re.findall(r'_{3,}', text)
    if blanks:
        issues.append(f"残留空白下划线: {len(blanks)}处")
    # 扫描残留的占位符
    placeholders = re.findall(r'\{(\w+)\}', text)
    if placeholders:
        issues.append(f"残留占位符: {placeholders}")
    # 扫描未填的空白行
    empty_lines = re.findall(r'(?<=\n)\s{10,}(?=\n)', text)
    if empty_lines:
        issues.append(f"疑似未填充的空白行: {len(empty_lines)}处")
    return issues


# ---------------------------------------------------------------------------
# 固定格式小节截取（从 format_section_text 中按数字标题定位单个小节）
# ---------------------------------------------------------------------------

# 数字章节标题，例如「二、投标函」「十一、商务文件其他材料」
SECTION_HEADER_RE = re.compile(
    r'^[ \t]*([一二三四五六七八九十]{1,3}[、．\.])([^\n]{1,80})$',
    re.MULTILINE,
)

# 独占一行的页码，如「-72-」「72」「— 72 —」。
# 用于判断标题行后面到底有没有正文（目录条目后面只有页码或什么都没有）。
_PAGE_NUMBER_LINE_RE = re.compile(r'^[\s\-—–]*\d+[\s\-—–]*$', re.MULTILINE)


def _normalize_title(s: str) -> str:
    """归一化标题：去空白、转小写，方便等价匹配."""
    return re.sub(r'\s+', '', s).strip().lower()


# 标题行前的编号，如「二、」「十一．」
_TITLE_NUM_PREFIX_RE = re.compile(r'^[一二三四五六七八九十]{1,3}[、．\.]')


def _strip_duplicate_heading(section_text: str, section_title: str) -> str:
    """去掉小节开头与章节标题重复的标题行.

    招标原文里每个固定格式小节都以两行标题起头（p76 实测「二、投标函」+「投标函」），
    而标书渲染（render_engine 的 level-1 heading / 前端章节树）本来就会单独渲染一次
    章节标题——原样回填，成品里就是三行「投标函」。

    只去开头**完全相等**的标题行（可带编号前缀），正文里提到标题的行（如
    「投标函附录」）必须留着。
    """
    target = _normalize_title(section_title)
    lines = section_text.split("\n")
    head = 0
    while head < len(lines):
        stripped = lines[head].strip()
        if stripped and _normalize_title(_TITLE_NUM_PREFIX_RE.sub("", stripped)) == target:
            head += 1
            continue
        break
    return "\n".join(lines[head:]).strip()


def extract_fixed_form_section(format_section_text: str, section_title: str) -> str:
    """从格式章节全文中定位并截取指定固定格式小节.

    匹配「数字+顿号+标题」行（如「二、投标函」）或独立标题行（如「投标承诺书」），
    截取到下一数字章节标题前。

    Args:
        format_section_text: 完整的「投标文件格式」章节文本。
        section_title: 目标小节标题，如「投标函」「法定代表人授权委托书」。

    Returns:
        该小节的正文（开头的重复标题行已去掉）；找不到返回空字符串。
    """
    if not format_section_text or not section_title:
        return ""

    target_norm = _normalize_title(section_title)

    # 找所有数字章节标题
    headers = []
    for m in SECTION_HEADER_RE.finditer(format_section_text):
        headers.append({
            "pos": m.start(),
            "end": m.end(),
            "body": m.group(2).strip(),
            "body_norm": _normalize_title(m.group(2)),
        })

    def _body_of(idx: int) -> str:
        """标题行与下一个标题之间的正文（末节取到文末）."""
        start = headers[idx]["end"]
        stop = (
            headers[idx + 1]["pos"]
            if idx + 1 < len(headers)
            else len(format_section_text)
        )
        return format_section_text[start:stop]

    def _has_body(raw: str) -> bool:
        """剔除独占行的页码后是否还有正文.

        目录里的标题连续成行，标题后要么没有内容，要么只剩一个页码
        （如「十九、附件」后面是「-73-」）。真小节后面必有正文。
        """
        return bool(_PAGE_NUMBER_LINE_RE.sub("", raw).strip())

    # 匹配目标标题：完全相等或目标标题是标题行的子串（容忍"投标函" vs "二、投标函"）。
    # 必须跳过目录条目——它与真小节共用同一个标题正则，且在文中先出现；
    # 取首个匹配会截出「一、封面」这 4 个字当成整章内容。
    start_idx = None
    for i, h in enumerate(headers):
        if h["body_norm"] == target_norm or target_norm in h["body_norm"]:
            if not _has_body(_body_of(i)):
                continue
            start_idx = i
            break

    if start_idx is None:
        return ""

    section_start = headers[start_idx]["pos"]
    if start_idx + 1 < len(headers):
        section_end = headers[start_idx + 1]["pos"]
    else:
        section_end = len(format_section_text)

    section_text = format_section_text[section_start:section_end].strip()
    return _strip_duplicate_heading(section_text, section_title)


async def fill_fixed_form_section_from_template(
    section_title: str,
    format_section_text: str,
    format_tables: list[dict] | None = None,
    company_profile: dict | None = None,
    requirements: dict | None = None,
    ai_adapter=None,
) -> str:
    """从招标文件的格式章节原文模板中提取并填充指定固定格式小节.

    用户需求：保留输入 PDF 的完整措辞（不修改），只填空下划线/标签词。
    流程：
      1. 定位小节（``extract_fixed_form_section``）
      2. AI 扫描标注变量位置（``scan_and_mark_variables``）
      3. 批量填充（``batch_fill_text``）
      4. 返回填充后内容

    Args:
        section_title: 目标小节标题，如「投标函」「法定代表人授权委托书」。
        format_section_text: 完整的「投标文件格式」章节文本（来自 bid.py 提取）。
        format_tables: 格式章节中提取的表格数据。
        company_profile: 公司信息字典。
        requirements: 解析后的招标文件要求字典。
        ai_adapter: AI 适配器实例。

    Returns:
        填充后的小节文本。找不到该小节、AI 扫描失败、或格式章节为空时返回空字符串，
        由调用方走 ``generate_file_section`` 兜底。
    """
    if not format_section_text or not section_title:
        return ""

    section_text = extract_fixed_form_section(format_section_text, section_title)
    if not section_text:
        logger.info(
            "Section '%s' not found in format_section_text, caller should fallback",
            section_title,
        )
        return ""

    variables = build_variable_values(company_profile, requirements)

    # 提示 AI 哪些变量已有真实值（避免它凭空猜测）
    known_values = {k: v for k, v in variables.items() if v}

    scan_result = await scan_and_mark_variables(
        full_text=section_text,
        tables=format_tables or [],
        ai_adapter=ai_adapter,
        known_values=known_values,
    )

    if scan_result.get("warnings") and not scan_result.get("text_replacements"):
        logger.warning(
            "Scan for section '%s' returned no replacements (%s); caller should fallback",
            section_title, scan_result["warnings"],
        )
        return ""

    # 把 variable 值注入到 replacement（如果 AI 没填）
    enriched = []
    unknown_vars: list[str] = []
    for rep in scan_result.get("text_replacements", []):
        var = rep.get("var")
        if not var:
            continue  # 跳过标签行（如 {"original": "投标人名称：", "var": null}）
        rep_copy = dict(rep)
        if "value" not in rep_copy or rep_copy["value"] is None:
            if var in variables:
                # 已知变量但没取到值 —— 空串，交 batch_fill_text 跳过
                rep_copy["value"] = variables[var]
            else:
                # AI 自造的变量名（曾经以 [unknown_1] 的形式落进正文）
                unknown_vars.append(var)
                rep_copy["value"] = ""
        enriched.append(rep_copy)

    if unknown_vars:
        logger.warning(
            "Section '%s': AI marked %d variable(s) with no source, left untouched: %s",
            section_title, len(unknown_vars), unknown_vars,
        )

    filled_text = batch_fill_text(section_text, enriched)

    # 兜底：若填完后还有残留空白下划线，记录 warning（但仍返回结果）
    residual = post_scan(filled_text)
    if residual:
        logger.warning(
            "Section '%s' filled with residuals: %s",
            section_title, residual,
        )

    logger.info(
        "Filled fixed-form section '%s': %d chars, %d replacements applied",
        section_title, len(filled_text), len(enriched),
    )
    return filled_text


# ---------------------------------------------------------------------------
# AI-Powered File Section Generation
# ---------------------------------------------------------------------------
# Instead of scanning the tender document for variable positions and filling
# them (which is fragile when PDF text extraction is garbled), this approach
# uses AI to GENERATE each standard file section from scratch following
# Chinese bid document conventions.

FILE_SECTION_TEMPLATES = {
    "投标函": {
        "description": "正式投标函，致招标人，声明已阅读招标文件、承诺投标有效期、承诺不转包分包",
        "prompt_guidance": """撰写正式的投标函，格式如下：
1. 致：[招标人名称]
2. 正文：声明已仔细研究招标文件全部内容，愿意按招标文件要求提交投标文件
3. 承诺投标有效期（如120日历天）
4. 声明独立投标、无联合体
5. 承诺不挂靠、不串标围标、不弄虚作假
6. 中标承诺（按期签约、缴纳履约担保、按期履约、不转包分包）
7. 同意投标保证金没收情形
8. 落款：投标人名称（公章）、法定代表人或授权代理人（签字）、日期
使用正式、规范的中国投标书措辞。""",
    },
    "投标承诺书": {
        "description": "投标人诚信承诺书",
        "prompt_guidance": """撰写投标承诺书，内容包含：
1. 承诺遵循公开、公平、公正和诚实信用原则
2. 承诺提供的一切材料真实、有效、合法
3. 承诺不串通投标、不损害国家利益、社会公共利益和他人合法权益
4. 承诺不向招标人或评标委员会成员行贿
5. 承诺不以他人名义投标或弄虚作假骗取中标
6. 承诺不挂靠、不转让、不违法分包
7. 承诺不在投标中哄抬价格或恶意压价
8. 承诺不捏造事实、伪造材料进行投诉
9. 落款：投标人（公章）、法定代表人（签字）、日期、联系信息""",
    },
    "法定代表人身份证明": {
        "description": "法定代表人身份证明书",
        "prompt_guidance": """撰写法定代表人身份证明书，格式如下：
1. 标题：法定代表人身份证明
2. 正文：兹证明 [姓名] 同志系 [公司名称] 的法定代表人
3. 附：法定代表人身份证复印件（此处标注"[身份证复印件粘贴处]"）
4. 落款：投标人（公章）、日期
信息从输入数据中获取。""",
    },
    "法定代表人授权委托书": {
        "description": "法定代表人授权委托书",
        "prompt_guidance": """撰写法定代表人授权委托书，格式如下：
1. 标题：法定代表人授权委托书
2. 正文：本人 [法定代表人姓名] 系 [公司名称] 的法定代表人，现授权委托 [授权代理人姓名] 为我方代理人
3. 说明代理权限：签署、澄清、说明、补正、递交、撤回、修改投标文件，签订合同和处理有关事宜
4. 委托期限
5. 附：授权代理人身份证复印件（此处标注"[身份证复印件粘贴处]"）
6. 落款：投标人（公章）、法定代表人（签字）、授权代理人（签字）、日期
如未提供授权代理人姓名，使用"[待补充]"标记。""",
    },
    "招标服务费承诺书": {
        "description": "招标服务费支付承诺书",
        "prompt_guidance": """撰写招标服务费承诺书，格式如下：
1. 致：招标代理机构名称
2. 正文：承诺如中标，在收到中标通知书后按招标文件规定支付招标服务费
3. 违约条款：如拒付或违约，同意从投标保证金中按200%扣缴
4. 落款：承诺人（公章）、地址、邮编、电话、日期""",
    },
    "廉洁诚信承诺书": {
        "description": "廉洁诚信承诺书（商务部分专用）",
        "prompt_guidance": """撰写廉洁诚信承诺书，内容包含：
1. 承诺不向招标人、招标代理机构、评标专家及相关工作人员行贿
2. 承诺不围标串标、不弄虚作假
3. 承诺配合纪检监察部门的监督检查
4. 承诺如违反廉洁规定，接受取消中标资格、列入不良行为记录名单等处理
5. 落款：投标人（公章）、法定代表人（签字）、日期
此为商务部分所需文件，措辞正式规范。""",
    },
    "关联关系承诺书": {
        "description": "与招标人干部职工不存在关联关系的承诺书（商务部分专用）",
        "prompt_guidance": """撰写与招标人干部职工不存在关联关系的承诺书，内容为：
承诺投标人与招标人干部职工之间不存在任何关联关系（如亲属关系、股权关系、利益关系等），
如经查实存在虚假承诺，自愿接受取消中标资格、没收投标保证金等处理。
落款：投标人（公章）、法定代表人（签字）、日期。""",
    },
    "企业信誉承诺书": {
        "description": "企业信誉情况承诺书（资格审查部分专用）",
        "prompt_guidance": """撰写企业信誉情况承诺书，内容包含：
1. 承诺未被责令停业、暂扣或吊销执照
2. 承诺未进入清算程序或被宣告破产
3. 承诺未被列入"国家企业信用信息公示系统"严重违法失信企业名单
4. 承诺未被列入"信用中国"网站失信被执行人名单
5. 承诺未被列入烟草行业"黑名单"
6. 落款：投标人（公章）、法定代表人（签字）、日期
注意：此承诺书与廉洁诚信承诺书不同，不可混淆。""",
    },
    "项目人员承诺书": {
        "description": "项目人员承诺书（资格审查部分专用）",
        "prompt_guidance": """撰写项目人员承诺书，内容包含：
1. 承诺配备的项目人员数量符合招标文件要求
2. 承诺所有人员均签订劳动合同并缴纳社会保险
3. 承诺所有人员均无犯罪记录
4. 承诺持证人员证书真实有效
5. 落款：投标人（公章）、法定代表人（签字）、日期""",
    },
    "开标一览表": {
        "description": "开标一览表",
        "prompt_guidance": """撰写开标一览表，使用表格格式：
| 项目 | 内容 |
|:---|:---|
| 项目名称 | [项目名称] |
| 投标报价（含税） | [金额] 元 |
| 投标保证金 | [金额] 元 |
| 服务期限 | [期限] |
| 服务质量 | 满足招标文件要求 |
| 项目地点 | [地点] |
注意：投标报价金额从输入数据中获取，如未提供使用"[待补充]"。""",
    },
}

FILE_SECTION_SYSTEM_PROMPT = """你是投标文件撰写专家。你的任务是根据输入的真实公司信息和项目信息，撰写标准的投标文件章节。

写作要求：
1. 严格按照中国招投标文件规范格式撰写
2. 所有公司信息（名称、法定代表人、统一社会信用代码、地址等）从输入数据中原样使用
3. 未提供的信息使用"[待补充]"标记，严禁编造
4. 使用正式、规范的中文投标书措辞和格式
5. 签章栏（落款）必须完整包含：投标人（公章）、法定代表人或授权代理人（签字）、日期
6. 直接返回可以放入投标文件中的内容，不要加任何解释性文字"""


async def generate_file_section(
    section_type: str,
    company_profile: dict | None = None,
    requirements: dict | None = None,
    project_name: str = "",
    ai_adapter=None,
) -> str:
    """Use AI to generate a properly formatted file section.

    Args:
        section_type: One of the keys in FILE_SECTION_TEMPLATES
            (e.g. "投标函", "法定代表人身份证明", etc.)
        company_profile: Company info dict with company_name, legal_rep_name, etc.
        requirements: Parsed tender requirements dict.
        project_name: Project name from tender document.
        ai_adapter: AI adapter instance for chat_completion.

    Returns:
        Generated section content as a formatted string.
    """
    template = FILE_SECTION_TEMPLATES.get(section_type)
    if not template:
        logger.warning("Unknown file section type: %s, skipping", section_type)
        return ""

    # ── Build context with real data ──
    from app.services.ai_pipeline import build_company_info_block

    context_parts = []
    company_block = build_company_info_block(company_profile)
    if company_block:
        context_parts.append(company_block)

    if project_name:
        context_parts.append(f"招标项目名称：{project_name}")

    if requirements:
        t_name = (requirements.get("tenderer_name") or "").strip()
        if t_name:
            context_parts.append(f"招标人（致函对象）：{t_name}")
        else:
            # Do NOT fall back to project_name — it would let the AI
            # hallucinate a company name. Tell the AI explicitly.
            context_parts.append(
                "招标人（致函对象）：[待补充：招标人公司全称]"
                "（严禁使用项目名称替代，严禁编造）"
            )
        if requirements.get("project_duration"):
            context_parts.append(f"服务期限：{requirements['project_duration']}")
        if requirements.get("project_budget"):
            context_parts.append(f"项目预算：{requirements['project_budget']}")

    context = "\n".join(context_parts)

    user_prompt = f"""请撰写以下投标文件章节。

【章节类型】{section_type}
【章节说明】{template['description']}

{template['prompt_guidance']}

【真实项目数据】
{context}

重要提醒：
- 公司名称、法定代表人、统一社会信用代码等必须使用上述真实数据
- 未提供的信息使用"[待补充]"标记
- 不要在内容中使用 markdown 标题符号（# ## ###），因为系统会自动设置标题层级
- 表格使用管道格式（| 列1 | 列2 |）
- 直接返回章节内容，不要加任何解释"""

    try:
        response = await ai_adapter.chat_completion(
            messages=[
                {"role": "system", "content": FILE_SECTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=4096,
        )
        return response
    except Exception as exc:
        logger.error("AI file section generation failed for '%s': %s", section_type, exc)
        return ""


async def generate_all_file_sections(
    company_profile: dict | None = None,
    requirements: dict | None = None,
    project_name: str = "",
    ai_adapter=None,
) -> dict:
    """Generate all standard file sections for the bid document.

    Returns a dict mapping section title → generated content.
    The order follows the standard Chinese bid document structure.
    """
    if not ai_adapter:
        logger.error("No AI adapter available for file section generation")
        return {}

    # Standard file sections in the order they appear in the bid
    section_types = [
        "投标函",
        "投标承诺书",
        "法定代表人身份证明",
        "法定代表人授权委托书",
        "招标服务费承诺书",
        "开标一览表",
    ]

    generated = {}
    for st in section_types:
        try:
            content = await generate_file_section(
                section_type=st,
                company_profile=company_profile,
                requirements=requirements,
                project_name=project_name,
                ai_adapter=ai_adapter,
            )
            if content:
                generated[st] = content
                logger.info("Generated file section: %s (%d chars)", st, len(content))
        except Exception as exc:
            logger.warning("Failed to generate file section '%s': %s", st, exc)

    return generated


# ---------------------------------------------------------------------------
# 开标一览表：纯项目数据驱动的固定表格生成器（不调用 AI，避免金额编造）
# ---------------------------------------------------------------------------

def build_bid_opening_table(
    project_name: str = "",
    tender_number: str = "",
    company_name: str = "",
    unit_price: str = "",
    unit_price_currency: str = "人民币，元/月",
    quantity_months: str = "11",
    total_price: str = "",
    vat_rate: str = "",
    service_location: str = "",
    service_period: str = "",
    invoice_type: str = "增值税专用发票",
    bid_deposit_method: str = "",
    bid_deposit_amount: str = "",
    remarks: str = "",
) -> str:
    """根据项目数据直接生成开标一览表的 markdown 表格内容.

    严格按招标文件"投标文件格式"中开标一览表的列定义（11 行 + 备注 + 签章），
    不调用 AI，避免大模型编造金额。

    Returns:
        一个 markdown 表格字符串（含表头、表体、备注、签章块）。
    """
    rows = [
        ("项目名称", project_name or "[待补充：项目名称]"),
        ("招标编号", tender_number or "[待补充：招标编号]"),
        ("投标人名称", company_name or "[待补充：投标人名称]"),
        (
            "不含税单价",
            f"小写：{unit_price or '[待补充]'}\n（币种：{unit_price_currency}）",
        ),
        ("数量（月）", quantity_months or "[待补充]"),
        (
            "不含税总价",
            f"小写：{total_price or '[待补充]'}\n（币种：人民币，元）",
        ),
        ("增值税税率（%）", vat_rate or "[待补充]"),
        ("服务地点", service_location or "[待补充：服务地点]"),
        ("服务期限", service_period or "[待补充：服务期限]"),
        ("发票类型", invoice_type or "增值税专用发票"),
        (
            "投标保证金",
            f"递交方式：{bid_deposit_method or '[待补充]'}\n金额：{bid_deposit_amount or '[待补充]'} 元",
        ),
    ]

    # Build markdown table (2 columns: 项目 / 内容)
    lines = ["| 项目 | 内容 |", "|:---|:---|"]
    for label, content in rows:
        # Escape pipe chars in content
        content_escaped = content.replace("|", "\\|").replace("\n", "<br>")
        lines.append(f"| {label} | {content_escaped} |")

    md = "\n".join(lines)

    # Append fixed remarks from tender spec
    md += "\n\n"
    md += "**注：**\n"
    md += "1. 此表应放于投标文件封面后第一页。\n"
    md += "2. 投标报价包含但不限于完成合同规定的全部工作所需支付的一切成本（管理）费用和拟获得的利润，并考虑应承担的人工及物价费用涨跌幅的风险。该费用包括但不限于人工费、被装费、加班费、劳保费、保险及管理费、通信设备、交通工具、应急装备、警卫器械、相关易耗品、办公设备、利润、规费、税金（增值税除外）等为实施和完成服务工作所需的全部费用、政策性文件规定、执行过程中所有风险以及合同包含的所有风险、责任等。\n"
    md += "3. 不含税总价=不含税单价*数量。\n"
    md += "4. 投标报价最多保留两位小数。\n"

    if remarks:
        md += f"\n**备注：** {remarks}\n"

    # Signature
    md += "\n\n"
    md += "投 标 人： （盖章）\n\n"
    md += "法定代表人或其委托代理人： （签字或盖章）\n\n"
    md += "日 期： 年 月 日\n"

    return md


def extract_bid_opening_data(
    project=None,
    requirements: dict | None = None,
    company_profile: dict | None = None,
    tender_format_section: dict | None = None,
) -> dict:
    """从项目数据中提取开标一览表所需的字段.

    Returns:
        dict with keys matching build_bid_opening_table kwargs.
    """
    requirements = requirements or {}
    company_profile = company_profile or {}

    # 招标编号 from project or requirements
    tender_number = ""
    if project is not None:
        tender_number = getattr(project, "tender_number", "") or ""
    if not tender_number:
        # parse from format_section.tables or requirements
        tender_number = (
            requirements.get("tender_number")
            or requirements.get("bid_number")
            or requirements.get("招标编号")
            or ""
        )

    # 项目名称
    project_name = ""
    if project is not None:
        project_name = getattr(project, "name", "") or ""
    if not project_name:
        project_name = requirements.get("project_name") or ""

    # 公司名
    company_name = (
        company_profile.get("company_name")
        or requirements.get("company_name")
        or (getattr(project, "company_name", "") if project is not None else "")
        or ""
    )

    # 报价 - 多个字段都尝试
    unit_price = (
        requirements.get("unit_price_excluding_tax")
        or requirements.get("monthly_unit_price")
        or requirements.get("单价")
        or ""
    )
    total_price = (
        requirements.get("total_price_excluding_tax")
        or requirements.get("total_bid_price")
        or requirements.get("投标报价")
        or requirements.get("bid_price")
        or ""
    )
    vat_rate = (
        requirements.get("vat_rate")
        or requirements.get("增值税税率")
        or ""
    )

    # 服务期限
    service_period = (
        requirements.get("project_duration")
        or requirements.get("service_period")
        or requirements.get("服务期限")
        or ""
    )

    # 服务地点
    service_location = (
        requirements.get("service_location")
        or requirements.get("project_location")
        or requirements.get("服务地点")
        or ""
    )

    # 投标保证金
    bid_deposit_amount = (
        requirements.get("bid_deposit_amount")
        or requirements.get("投标保证金金额")
        or ""
    )
    bid_deposit_method = (
        requirements.get("bid_deposit_method")
        or requirements.get("投标保证金递交方式")
        or "电汇"
    )

    # 数量（月）
    quantity_months = str(requirements.get("quantity_months") or requirements.get("数量") or "11")

    # 发票类型
    invoice_type = (
        requirements.get("invoice_type")
        or requirements.get("发票类型")
        or "增值税专用发票"
    )

    return {
        "project_name": project_name,
        "tender_number": tender_number,
        "company_name": company_name,
        "unit_price": unit_price,
        "quantity_months": quantity_months,
        "total_price": total_price,
        "vat_rate": vat_rate,
        "service_location": service_location,
        "service_period": service_period,
        "invoice_type": invoice_type,
        "bid_deposit_method": bid_deposit_method,
        "bid_deposit_amount": bid_deposit_amount,
        "remarks": requirements.get("remarks", ""),
    }
