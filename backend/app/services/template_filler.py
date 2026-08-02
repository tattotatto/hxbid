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
        if requirements.get("tenderer_name") or requirements.get("project_name"):
            t_name = requirements.get("tenderer_name") or requirements.get("project_name", "")
            context_parts.append(f"招标人（致函对象）：{t_name}")
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
