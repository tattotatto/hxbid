"""宏曦标书 - 模板填充引擎.

完整提取的格式章节全文 → AI 标注变量位置 → 批量替换 → 生成填充后的文档内容.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import re
import json
import logging
from datetime import date
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

SCAN_SYSTEM_PROMPT = """你是投标文件分析专家。招标文件的格式章节已完整提取。
你的任务是扫描全文，找出所有需要投标人**填写**的位置，标注变量名。

变量名只能从以下列表选取：
- company_name: 投标人公司名称
- legal_rep_name: 法定代表人姓名
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
- tenderer_name: 招标人名称
- date: 日期
- bid_validity_days: 投标有效期天数

如果你不确定某个位置该对应哪个变量，用 unknown_1, unknown_2 等标记，并在 warnings 中说明。

表格中每个空单元格如果已有标签行标明该填什么，标注对应的变量名。

返回 JSON:
{
  "text_replacements": [
    {"original": "________", "var": "company_name", "context_before": "投标人名称："},
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
) -> dict:
    """AI 扫描全文，标注所有变量位置."""
    tables_json = json.dumps(tables[:10], ensure_ascii=False)  # 限制表格数量
    prompt = f"""请扫描以下招标文件格式章节，找出所有需要投标人填写的位置。

全文（含封面、目录、正文）：
---
{full_text[:15000]}
---

表格数据：
---
{tables_json}
---

请标注每个填写位置对应的变量名。直接返回JSON，不要其他文字。"""

    try:
        response = await ai_adapter.chat_completion(
            messages=[
                {"role": "system", "content": SCAN_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=8192,
            response_format={"type": "json_object"},
        )
        result = json.loads(response)
    except (json.JSONDecodeError, Exception) as exc:
        logger.error("Variable scanning failed: %s", exc)
        return {
            "text_replacements": [],
            "table_fills": [],
            "warnings": [f"AI扫描失败: {exc}"],
        }

    return result


def build_variable_values(
    company_profile: dict | None = None,
    requirements: dict | None = None,
) -> dict:
    """构建变量值映射."""
    company = company_profile or {}
    reqs = requirements or {}

    return {
        "company_name": company.get("company_name") or "[待补充]",
        "legal_rep_name": company.get("legal_rep_name") or "[待补充]",
        "business_license_number": company.get("business_license_number") or "[待补充]",
        "address": company.get("address") or "[待补充]",
        "contact_phone": company.get("contact_phone") or "[待补充]",
        "website": company.get("website") or "",
        "contact_person": company.get("contact_person") or "[待补充]",
        "fax": company.get("fax") or "",
        "zip_code": company.get("zip_code") or "",
        "registered_capital": company.get("registered_capital") or "",
        "account_number": company.get("account_number") or "",
        "bank_name": company.get("bank_name") or "",
        "project_name": reqs.get("project_name") or "[待补充]",
        "tenderer_name": reqs.get("project_name") or "[待补充]",
        "date": date.today().strftime("%Y年%m月%d日"),
        "bid_validity_days": "120",
    }


def batch_fill_text(text: str, text_replacements: list[dict]) -> str:
    """批量文本替换：将原文中的空白/占位符替换为实际值.

    按 replacement 的长度降序排列，避免短串先替换破坏长串。
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
        original = rep.get("original", "")
        if var and original:
            value = rep.get("value", f"[{var}]")
            result = result.replace(original, value, 1)  # 逐个替换，避免错误匹配

    return result


def batch_fill_tables(tables: list[dict], table_fills: list[dict], variables: dict) -> list[dict]:
    """批量表格填充：在指定位置填入变量值."""
    result = [{"page": t["page"], "table_index": t["table_index"], "rows": [list(row) for row in t["rows"]]} for t in tables]

    for fill in table_fills:
        page = fill.get("page")
        ti = fill.get("table_index")
        row = fill.get("row")
        col = fill.get("col")
        var = fill.get("var", "")
        value = variables.get(var, f"[{var}]")

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
