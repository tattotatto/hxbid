"""宏曦标书 - AI Pipeline Orchestration Engine.

Core AI orchestration module that coordinates bid document analysis,
outline generation, and chapter content creation. All AI calls go through
the ai_adapter singleton; PII is de-identified before entering prompt context.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import asyncio
import datetime
import json
import logging
import re
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import async_session
from app.services.ai_adapter import ai_adapter
from app.services.deid import deidentify_text
from app.services.materials_context import (
    CONTRACT_KEYWORDS,
    PERSONNEL_CTX_KEYWORDS,
    QUAL_CTX_KEYWORDS,
    assemble_section_materials,
    build_personnel_context,
    build_qualifications_context,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# 匹配 markdown 标题行（# 至 ####），用于裁掉正文末尾的孤立标题
_TRAILING_HEADING_RE = re.compile(r"^#{1,4}\s+\S")


def _strip_trailing_headings(text: str) -> str:
    """裁掉正文末尾的孤立标题行.

    AI 在预算耗尽时可能以标题行收尾（标题后无正文），组装后会被空标题校验判定为
    "空标题"（标题紧跟下一标题）。这里把末尾连续的空行和标题行清掉，保证每节都
    以正文收尾；若整节只有标题（极端情况），保留原样，避免把整节内容清空。
    """
    if not text or not text.strip():
        return text
    lines = text.rstrip().split("\n")
    while True:
        end = len(lines)
        while end > 0 and not lines[end - 1].strip():
            end -= 1
        if end == 0:
            break
        if _TRAILING_HEADING_RE.match(lines[end - 1].strip()):
            lines = lines[: end - 1]
            continue
        break
    stripped = "\n".join(lines).strip()
    return stripped or text

SYSTEM_PROMPT = """你是投标书撰写系统的AI助手，专注于为投标人撰写保安/物业服务类投标文件。

写作规范：
1. 使用中文标书行业地道表达，避免使用"首先""其次""此外""总而言之"等模板化连接词
2. 每个段落必须包含至少1个具体事实（数字、日期、项目名、证书编号等）
3. 禁止使用"经验丰富""技术精湛""服务周到""管理能力强"等空泛形容词和套话
4. 对招标文件的每个要求必须作出针对性回应，措辞不能照搬原文，要用自己的话表达
5. 句式结构多样化，相邻段落开头不能雷同
6. 标题层级规范（极其重要）：使用 ## 和 ### 标记标题层级，标题独占一行。
   - 章节内节标题（## 开头）：如"## 一、服务方案"、"## 二、应急预案"、"## 三、人员培训方案"
   - 小节标题（### 开头）：如"### 1. 日常安保方案"、"### 2. 消防应急响应流程"、"### 3. 季度演练安排"
   重要说明：
   - 所有 ## 和 ### 标题将自动渲染为Word多级标题（二号标题和三号标题），目录将包含全部三级标题
   - 不要在内容中使用单个 #，因为章节大标题（如"商务部分""技术部分"）已由系统自动设置为一级标题
   - 【引导段先行（极其重要）】每个 ##、###、#### 标题之后，必须立即写一段引导/综述文字
     （1-2 句说明该标题涵盖什么、与上下文的衔接），再继续写子标题或分点。绝不允许出现
     "标题之后紧跟另一个标题"的空标题现象
   - 每个 ## 节下面至少写2-3段正文，再根据需要添加 ### 小节
   - 每个段落至少包含1个可量化事实（人数/天数/频率/金额/编号/日期等），禁止空泛堆砌
   - 本节标题必须以正确层级输出为第一个标题（如当前节是"## 一、服务方案"，第一行就是
     "## 一、服务方案"），不得省略、不得改用错误层级
   - 确保全文标题序号连续且不重复
7. 输出纯文本内容。呈现表格数据时必须使用以下两种格式之一：
   a) 【推荐】Markdown管道表格：先写"表X：标题"作为独立一行，然后使用管道表格格式。示例：
      表1：项目人员一览表
      | 姓名 | 年龄 | 学历 | 证书 | 从业年限 | 拟任角色 |
      |:---|:---|:---|:---|:---|:---|
      | 张三 | 36 | 本科 | 消防员证 | 12年 | 队长 |
      | 李四 | 33 | 大专 | 电工证 | 10年 | 副队长 |
      注意：表头和表体之间必须有分隔行（|:---|:---|...|），每个单元格左右各有一个空格
   b) 分号分隔键值对：每行一条记录，各字段用分号分隔，键值用中文冒号连接。示例：
      表2：项目人员一览表
      姓名：张三；年龄：36岁；学历：本科；证书：消防员证；从业年限：12年；拟任角色：队长
      姓名：李四；年龄：33岁；学历：大专；证书：电工证；从业年限：10年；拟任角色：副队长
   表格将在Word文档中自动渲染为带边框的真实表格，方便阅读
8. 公司基本信息（公司名称、法定代表人、统一社会信用代码、地址、联系电话等）必须使用输入中提供的真实数据原文照搬，严禁编造或改写。禁止凭空编造任何人名（法定代表人、授权代表、项目负责人等），这些信息只能从输入提供的资料中获取。如某项信息在输入资料中未提供，对应位置留空，不得写"[待补充]"之类的占位标记，更不得自行编造

章节内容分配规范（极其重要）：
- 商务部分：投标函、法定代表人证明、授权委托书、投标保证金凭证、廉洁诚信承诺书、与招标人干部职工不存在关联关系的承诺书
- 技术部分：人员配置表及证明材料、服务方案、培训方案、应急预案、服务承诺
- 资格审查部分：公司基本情况表、营业执照复印件、经营许可证/资质证书复印件、企业信誉情况承诺书、项目人员承诺书
- 投标人认为需要提供的其他内容：前三部分未覆盖的补充材料（获奖证书、类似业绩合同、认证证书等证明履约能力的材料）。严格禁止重复前三个部分已有的任何承诺书或资质证照
- 廉洁诚信承诺书只在商务部分出现一次，不得在其他章节重复
- 与招标人干部职工不存在关联关系的承诺书只在商务部分出现一次，不得在其他章节重复
- 资格审查部分的"企业信誉情况承诺书"与商务部分的"廉洁诚信承诺书"是不同文件，不可混淆

严禁编造规则（最高优先级）：
- 用户输入中提供的【公司基本信息】是唯一合法的数据来源，标书中所有公司名称、法定代表人姓名、统一社会信用代码、地址等必须与之一致
- 人员姓名只能从用户输入中提供的"可用项目人员"列表中选取，不得编造不存在的人员
- 资质证书名称和编号只能从用户输入中提供的"可用资质证书"列表中选取
- 如果某项信息在输入资料中标记为[未填写]或未提供，标书中对应位置留空，不得写"[待补充]"之类的占位标记，严禁自行编造填充"""

DEFAULT_BID_SECTIONS = [
    "投标函及投标函附录",
    "法定代表人身份证明",
    "授权委托书",
    "投标保证金",
    "公司资质与业绩",
    "项目人员配置方案",
    "服务方案与技术方案",
    "应急预案",
    "培训计划",
    "报价明细",
    "服务承诺",
]

MAX_INPUT_CHARS = 15000


def build_company_info_block(company: dict | None) -> str:
    """Build a structured text block of real company info for AI prompts.

    Returns empty string if no company data is available. The block is
    prefixed with a strong instruction that this data MUST be used verbatim
    and never fabricated.
    """
    if not company:
        return ""

    lines = [
        "【公司基本信息 — 以下为真实数据，必须在标书中原样使用，严禁编造或修改】",
    ]

    fields = [
        ("公司名称", "company_name"),
        ("统一社会信用代码", "business_license_number"),
        ("法定代表人", "legal_rep_name"),
        ("法定代表人身份证号", "legal_rep_id_number"),
        ("公司地址", "address"),
        ("联系电话", "contact_phone"),
        ("公司网站", "website"),
    ]

    for label, key in fields:
        value = (company.get(key) or "").strip()
        if value:
            lines.append(f"  {label}：{value}")
        else:
            lines.append(f"  {label}：[未填写]")

    # Include notes if present
    notes = (company.get("notes") or "").strip()
    if notes:
        lines.append(f"  备注：{notes}")

    lines.append("")
    lines.append("重要提醒：标书中涉及上述信息时，必须使用以上真实数据，不得自行编造任何公司名称、人员姓名、证照编号等信息。如某字段标注为[未填写]，请在标书中留空（不写[待补充]之类的占位标记），不得编造。")

    return "\n".join(lines)


# Cache for active constraints (refreshed each generation session)
_active_constraints_cache: List[str] = []
_constraints_cache_version: int = 0


async def _get_active_constraints() -> List[str]:
    """Load active prompt constraints from the feedback loop.

    Cached in-process; refreshed when called from the API handler.
    """
    global _active_constraints_cache, _constraints_cache_version
    try:
        from app.database import async_session
        from app.services.feedback_loop import get_active_prompt_constraints

        async with async_session() as db:
            constraints = await get_active_prompt_constraints(db)
            _active_constraints_cache = constraints
            _constraints_cache_version += 1
            return constraints
    except Exception as exc:
        logger.debug("Failed to load active constraints: %s", exc)
        return _active_constraints_cache


def _build_system_prompt(
    extra_constraints: List[str] | None = None,
    format_template: dict | None = None,
) -> str:
    """Build the full system prompt, appending any active feedback rules.

    When format_template is provided, global format rules (numbering style,
    TOC heading title, page number format) are injected as hard constraints.
    """
    parts = [SYSTEM_PROMPT]
    all_constraints = list(_active_constraints_cache)
    if extra_constraints:
        all_constraints.extend(extra_constraints)
    if all_constraints:
        parts.append("\n额外写作约束（基于历史编辑反馈自动生成）：")
        for i, c in enumerate(all_constraints, 1):
            parts.append(f"  {i}. {c}")

    # ── Global format rules from tender document ──
    if format_template and format_template.get("global_format_rules"):
        global_rules = format_template["global_format_rules"]
        numbering = global_rules.get("numbering_style", "")
        parts.append("\n【招标文件规定的格式规范 — 硬性要求】")
        if numbering:
            if numbering == "chinese_legal":
                parts.append("- 序号体系：一级用中文数字（一、二、三...），二级用带括号中文数字（（一）、（二）...），三级用阿拉伯数字加点（1.、2.）")
            elif numbering == "numeric":
                parts.append("- 序号体系：一级用阿拉伯数字（1、2、3...），二级用（1.1、1.2...）")
        if global_rules.get("toc_heading_title"):
            parts.append(f"- 目录页标题为：{global_rules['toc_heading_title']}")
        if global_rules.get("page_number_format"):
            parts.append(f"- 页码格式：{global_rules['page_number_format']}")
        parts.append("- 以上格式要求来自招标文件原文，生成内容时必须严格遵守。")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Helper: build messages list with system prompt prepended
# ---------------------------------------------------------------------------

def _build_messages(
    user_content: str,
    extra_constraints: List[str] | None = None,
    format_template: dict | None = None,
) -> List[Dict[str, str]]:
    """Return a messages list with SYSTEM_PROMPT as the system message."""
    return [
        {"role": "system", "content": _build_system_prompt(extra_constraints, format_template)},
        {"role": "user", "content": user_content},
    ]


def _requirements_summary(requirements: dict) -> str:
    """Format a requirements dict into a concise summary string for prompts."""
    parts: List[str] = []

    if requirements.get("project_name"):
        parts.append(f"项目名称：{requirements['project_name']}")
    if requirements.get("project_budget"):
        parts.append(f"项目预算：{requirements['project_budget']}")
    if requirements.get("project_duration"):
        parts.append(f"项目期限：{requirements['project_duration']}")

    qual_reqs = requirements.get("qualification_requirements", [])
    if qual_reqs:
        parts.append(f"资质要求：{'；'.join(qual_reqs)}")

    personnel = requirements.get("personnel_requirements")
    if personnel:
        parts.append(f"人员要求：{personnel}")

    service_reqs = requirements.get("service_requirements", [])
    if service_reqs:
        parts.append(f"服务要求：{'；'.join(service_reqs)}")

    eval_criteria = requirements.get("evaluation_criteria")
    if eval_criteria:
        parts.append(f"评标标准：{eval_criteria}")

    special = requirements.get("special_requirements", [])
    if special:
        parts.append(f"特殊要求：{'；'.join(special)}")

    # New structured fields for the information-collection step
    required_docs = requirements.get("required_documents", [])
    if required_docs:
        doc_names = [d["name"] if isinstance(d, dict) else str(d) for d in required_docs]
        parts.append(f"需提供证件：{'；'.join(doc_names)}")

    required_personnel = requirements.get("required_personnel", [])
    if required_personnel:
        personnel_desc = []
        for p in required_personnel:
            if isinstance(p, dict):
                role = p.get("role", "")
                certs = p.get("certifications", [])
                cnt = p.get("count", 1)
                cert_str = f"（需持{'、'.join(certs)}）" if certs else ""
                cnt_str = f" x{cnt}" if cnt > 1 else ""
                personnel_desc.append(f"{role}{cert_str}{cnt_str}")
            else:
                personnel_desc.append(str(p))
        parts.append(f"人员配置：{'；'.join(personnel_desc)}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 1. parse_bid_requirements
# ---------------------------------------------------------------------------

async def parse_bid_requirements(document_text: str, max_tokens: Optional[int] = None) -> dict:
    """Parse bidding document text into structured requirements via AI.

    Truncates input to MAX_INPUT_CHARS characters before sending to the model.
    Uses JSON response_format for guaranteed structured output.

    Args:
        document_text: Raw text extracted from the bidding document.

    Returns:
        dict with keys: project_name, project_budget, project_duration,
        qualification_requirements, personnel_requirements,
        service_requirements, evaluation_criteria, special_requirements,
        bid_sections.
    """
    truncated = document_text[:MAX_INPUT_CHARS]

    user_prompt = f"""请分析以下招标文件内容，提取关键信息并以JSON格式返回。

要求提取的字段：
- project_name: 项目名称（字符串）
- project_budget: 项目预算（字符串，如未提及则为空字符串）
- project_duration: 项目期限/服务期限（字符串，如未提及则为空字符串）
- qualification_requirements: 资质要求列表（字符串数组）
- personnel_requirements: 人员配置要求（字符串，概述人员数量、持证要求等）
- service_requirements: 服务内容要求列表（字符串数组）
- evaluation_criteria: 评标办法/评标标准（字符串）
- special_requirements: 特殊要求列表（字符串数组，如保密要求、特殊设备等）
- bid_sections: 招标文件要求的标书章节/组成部分列表（字符串数组，按招标文件规定的顺序排列）
- required_documents: 招标文件明确要求提供的证件/资质文件列表（对象数组，每个对象包含 name 证件名称 和 category 类别）
  例如：[{{"name": "营业执照", "category": "company"}}, {{"name": "保安服务许可证", "category": "qualification"}}]
  category 取值为: "company"（公司基础证照）、"qualification"（专业资质证书）、"financial"（财务证明）、"other"
- required_personnel: 招标文件要求配置的项目人员列表（对象数组，每个对象包含 role 岗位名称、certifications 持证要求数组、count 需求人数）
  例如：[{{"role": "项目负责人", "certifications": ["保安师证"], "count": 1}}]
  count 默认为 1
- tenderer_name: 招标人/采购人/发包人/业主的公司全称（字符串，**只填公司名，不要包含项目名称**）
  例如："玉溪大红山矿业有限公司"
  区分：招标人/采购人/发包人/业主/甲方 → tenderer_name；招标代理机构 → tenderer_agency_name；项目名称 → project_name
  如果招标文件中"招标人"一词仅出现项目名语境而无明确公司主体，则留空字符串
- tenderer_agency_name: 招标代理机构名称（字符串，未提及留空）
- tender_number: 招标编号/项目编号（字符串，未提及留空）
  例如："YXDHS-2026-001"
  只填编号本身，不要把项目名称或招标人名称混进来
- service_location: 服务地点/项目地点/服务实施地点（字符串，未提及留空）
- bid_deposit_amount: 投标保证金金额（字符串，只填金额数字，如 "50000"；未提及留空）

注意：
- 所有字段都必须存在，未提及的字段使用空字符串或空数组
- 直接返回JSON对象，不要包含任何其他文字说明

招标文件内容：
{truncated}"""

    messages = _build_messages(user_prompt)

    try:
        response = await ai_adapter.chat_completion(
            messages=messages,
            temperature=0.3,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        logger.error("AI call failed in parse_bid_requirements: %s", exc)
        response = ""

    try:
        result = json.loads(response) if response else {}
    except json.JSONDecodeError:
        result = {}
    if not isinstance(result, dict):
        result = {}

    # Return a safe default structure on parse failure (or AI failure above)
    if not result:
        return {
            "project_name": "",
            "project_budget": "",
            "project_duration": "",
            "qualification_requirements": [],
            "personnel_requirements": "",
            "service_requirements": [],
            "evaluation_criteria": "",
            "special_requirements": [],
            "bid_sections": [],
            "required_documents": [],
            "required_personnel": [],
            "tenderer_name": "",
            "tenderer_agency_name": "",
            "tender_number": "",
            "service_location": "",
            "bid_deposit_amount": "",
        }

    # Ensure all expected keys are present with sane defaults
    defaults: Dict[str, Any] = {
        "project_name": "",
        "project_budget": "",
        "project_duration": "",
        "qualification_requirements": [],
        "personnel_requirements": "",
        "service_requirements": [],
        "evaluation_criteria": "",
        "special_requirements": [],
        "bid_sections": [],
        "required_documents": [],
        "required_personnel": [],
        "tenderer_name": "",
        "tenderer_agency_name": "",
        "tender_number": "",
        "service_location": "",
        "bid_deposit_amount": "",
    }
    for key, default in defaults.items():
        if key not in result:
            result[key] = default

    # Defensive fallback: AI may return empty tenderer_name even though the
    # document clearly identifies the 招标人/采购人/发包人/业主. Sweep the
    # original (non-truncated) document text for the company name and patch
    # the result if AI missed it. Never overwrites a non-empty AI value.
    if not result.get("tenderer_name"):
        fallback = _extract_tenderer_name_defensive(document_text)
        if fallback:
            result["tenderer_name"] = fallback
            logger.info(
                "tenderer_name filled by regex fallback: %s", fallback,
            )

    return result


def _extract_tenderer_name_defensive(document_text: str) -> str:
    """Regex fallback for tenderer_name when AI returns empty.

    Tenders phrase the buyer in several ways: 招标人 / 采购人 / 发包人 / 业主.
    We sweep the document text with progressively broader patterns and
    return the first reasonable company name. Conservative — returns ""
    when no confident match is found rather than guess.

    Rejects matches that contain "项目" because such matches are almost
    always the project title being misinterpreted as a company name.
    """
    # Order matters: most specific first.
    # Greedy `{4,60}` (not lazy `{4,60}?`) so the regex extends fully and
    # then backtracks to the FIRST matching company-suffix alternative.
    # Lazy quantifiers stop at the shortest match — e.g. for "业主：曲靖某
    # （集团）有限公司" the lazy form would capture "曲靖某（集团" because
    # the alternative `集团` matches at position 6 before `有限公司` ever
    # gets a chance. Greedy forces the engine to look further for the
    # longest alternative.
    patterns = [
        # 招标人（名称）：XX / 招标人：XX / 招标单位：XX
        re.compile(
            r'招\s*标\s*人[\s（(]?(?:名\s*称|单\s*位)?[）)\s]*[:：]'
            r'\s*([一-龥（）()·\s]{4,60}'
            r'(?:有限公司|有限责任公司|股份公司|集团|公司))'
        ),
        # 采购人：XX
        re.compile(
            r'采\s*购\s*人[\s（(]?(?:名\s*称|单\s*位)?[）)\s]*[:：]'
            r'\s*([一-龥（）()·\s]{4,60}'
            r'(?:有限公司|有限责任公司|股份公司|集团|公司))'
        ),
        # 发包人：XX
        re.compile(
            r'发\s*包\s*人[\s（(]?(?:名\s*称)?[）)\s]*[:：]'
            r'\s*([一-龥（）()·\s]{4,60}'
            r'(?:有限公司|有限责任公司|股份公司|集团|公司))'
        ),
        # 业主：XX
        re.compile(
            r'业\s*主[\s（(]?(?:名\s*称)?[）)\s]*[:：]'
            r'\s*([一-龥（）()·\s]{4,60}'
            r'(?:有限公司|有限责任公司|股份公司|集团|公司))'
        ),
    ]
    for pat in patterns:
        m = pat.search(document_text)
        if m:
            name = m.group(1).strip()
            if "项目" not in name:
                return name
    return ""


# ---------------------------------------------------------------------------
# 2. generate_outline
# ---------------------------------------------------------------------------

def generate_outline(requirements: dict) -> list[dict]:
    """Generate a bid document outline from parsed requirements.

    If the requirements already contain bid_sections extracted from the
    document, those are used directly. Otherwise falls back to the default
    security/property service bid section list.

    Args:
        requirements: Parsed requirements dict from parse_bid_requirements().

    Returns:
        List of dicts, each with "order_index" (int) and "title" (str).
    """
    sections = requirements.get("bid_sections") if requirements else None

    if not sections:
        sections = DEFAULT_BID_SECTIONS

    return [
        {"order_index": i + 1, "title": title}
        for i, title in enumerate(sections)
    ]


# ---------------------------------------------------------------------------
# Section-specific content guidance
# ---------------------------------------------------------------------------

def _get_section_guidance(chapter_title: str, format_template: dict | None = None) -> str:
    """Return content-structure guidance for a given chapter title.

    Bidding documents (招标文件) typically divide the bid into four parts
    (see 第六章 投标文件格式).  Each part has a clear purpose; without
    explicit guidance the AI tends to put commitment letters everywhere
    and duplicate content across sections.

    When format_template is provided, per-section format constraints
    (table columns, fixed-form text, signature blocks) are appended.
    """
    title_lower = chapter_title.strip().lower()

    # ── Determine base guidance by section type ──
    guidance = ""

    # -- 商务部分 / Business Section --
    if _match_section(title_lower, ["商务", "商务部分"]):
        guidance = """【本章节内容规范 — 商务部分】
本章节必须包含以下内容，按顺序排列：
1. 开标一览表（项目名称、不含税单价/总价、服务期限、税率、投标保证金）
2. 投标函（致招标人，声明已阅读招标文件、投标有效期、承诺不转包分包、承诺不串标围标）
3. 法定代表人身份证明书
4. 法定代表人授权委托书（如由授权代理人签署则提供）
5. 投标保证金缴纳凭证及基本账户证明
6. 廉洁诚信承诺书（承诺不贿赂、不串标、不弄虚作假，配合纪检监察，接受禁入措施）
7. 与招标人干部职工不存在关联关系的承诺书

注意：
- 廉洁诚信承诺书和关联关系承诺书只在本章节出现，其他章节不得重复
- 本章节不包含公司资质证书（资质证书在资格审查部分）
- 承诺书文本应完整、正式，包含投标人签章栏"""

    # -- 技术部分 / Technical Section --
    elif _match_section(title_lower, ["技术", "技术部分", "服务方案", "技术方案"]):
        guidance = """【本章节内容规范 — 技术部分】
本章节为项目技术方案，必须包含：
1. 项目投入服务人员一览表（姓名、年龄、学历、证书、从业年限、拟任角色）
2. 人员相关证明材料说明（劳动合同、社保证明、退出现役证、驾驶证、消防员证等）
3. 项目整体服务方案（对招标文件第三章"招标内容及要求"逐条响应）
4. 人员培训方案（含各服务内容操作流程培训）
5. 项目重点难点分析及应对措施
6. 人员保障方案（招聘、轮休、替补机制）
7. 演练计划方案（季度性实战演练安排）
8. 服务承诺（服务质量保证措施、违约责任承诺）
9. 队伍管理制度、器材车辆保养方案及应急预案

注意：
- 所有描述必须有具体数字（人数、天数、频率、距离、金额等）
- 不得包含廉洁诚信承诺书等商务部分内容"""

    # -- 资格审查部分 / Qualification Review Section --
    elif _match_section(title_lower, ["资格审查", "资格", "资质审查", "公司资质", "资质与业绩"]):
        guidance = """【本章节内容规范 — 资格审查部分】
本章节用于证明投标人具备投标资格，必须包含：
1. 投标人基本情况表（公司名称、统一社会信用代码、法定代表人、注册资本、成立时间、经营范围、公司简介）
2. 企业信誉情况承诺书（承诺：未被暂停投标资格、未列入严重失信名单、未列入行贿行为供应商名单）
3. 项目人员承诺书（承诺人员数量、资质、劳动合同、社保、无犯罪记录等符合招标要求）

【极其重要 — 证照图片处理规则】
- 营业执照、保安服务许可证、其他资质证书的扫描图片将由系统自动嵌入到本章节开头
- 你不需要描述证照内容，不需要写"附后""见附件"等字样，系统会自动处理
- 你只需撰写基本情况表、承诺书等文字内容即可
- 不得包含廉洁诚信承诺书（该承诺书在商务部分）
- 不得包含与招标人干部职工不存在关联关系的承诺书（该承诺书在商务部分）
- 企业信誉情况承诺书与廉洁诚信承诺书是不同的文件，不可混淆"""

    # -- 投标人认为需要提供的其他内容 / Other Materials --
    elif _match_section(title_lower, ["其他", "其他内容", "投标人认为需要提供的"]):
        guidance = """【本章节内容规范 — 投标人认为需要提供的其他内容】
本章节用于补充前三部分未覆盖的证明材料，例如：
1. 公司获奖证书、荣誉证明
2. 类似项目业绩合同关键页
3. ISO管理体系认证证书
4. 企业信用评级报告
5. 其他能证明投标人履约能力的补充材料

严格禁止：
- 不得重复商务部分已有的承诺书（廉洁诚信承诺书、关联关系承诺书）
- 不得重复资格审查部分已有的资质证照和企业信誉承诺书
- 不得重复技术部分已有的人员配置和服务方案
- 本章节的内容必须是在前三个部分中没有出现过的补充材料

如确实无补充材料，可简要声明"投标人已将所有相关证明材料分别归入商务部分、技术部分和资格审查部分，本处不再赘述。" """

    # -- 投标函 specific --
    elif _match_section(title_lower, ["投标函"]):
        guidance = """【本章节内容规范 — 投标函】
按招标文件格式撰写正式投标函：
1. 致招标人全称
2. 声明已仔细阅读全部招标文件内容
3. 承诺投标有效期（从投标截止日起120个日历天）
4. 声明独立投标、无联合体
5. 承诺不挂靠、不串标围标
6. 中标承诺（按期签约、缴纳履约担保、按期履约、不转包分包）
7. 同意投标保证金没收情形
8. 附投标人签章栏"""

    # -- 类似项目业绩 / Project Performance Section --
    elif _match_section(title_lower, ["业绩", "类似项目", "项目经验", "成功案例", "既往项目"]):
        guidance = """【本章节内容规范 — 公司业绩】
本章节展示投标人类似项目业绩，系统将自动插入"公司业绩一览表"和合同扫描图片。
你只需撰写以下文字内容：

## 公司业绩概述
（简要介绍公司过往项目经验，2-3段即可，说明公司在保安/物业服务领域的丰富经验）

## 项目质量管理
（说明公司如何确保每个项目的服务质量，1-2段）

注意：
- 不需要自己写表格，不需要写"项目详细情况"章节，系统会自动处理
- 严禁编造任何项目名称或数据"""

    # -- Append per-section format constraints from tender template --
    fmt_suffix = _build_section_format_guidance(chapter_title, [], format_template)
    if fmt_suffix:
        if guidance:
            return guidance + "\n" + fmt_suffix
        return fmt_suffix
    return guidance

def _match_section(title_lower: str, keywords: list) -> bool:
    """Return True if any keyword appears in the chapter title."""
    return any(kw in title_lower for kw in keywords)


def _build_section_format_guidance(
    section_title: str,
    section_path: list[str],
    format_template: dict | None,
) -> str:
    """Build per-section format constraint guidance from the tender format template.

    Matches the section title/path against the document_structure in the
    format_template. When a match is found, injects table column constraints,
    fixed-form text segments, and signature block requirements into a prompt
    guidance string.

    Returns an empty string when format_template is None or no match is found.
    """
    if not format_template:
        return ""

    parts = []
    structure = format_template.get("document_structure", [])

    for part in structure:
        for child in part.get("children", []):
            child_title = child.get("title", "")
            # Match: child title appears in section path or section title
            path_titles = " ".join(section_path)
            if child_title not in path_titles and child_title not in section_title:
                continue

            child_type = child.get("type") or "text"

            if child_type == "table":
                cols = [c["name"] for c in child.get("table_schema", {}).get("columns", [])]
                if cols:
                    parts.append(
                        f"\n【招标文件规定的表格格式 — 必须严格遵守】\n"
                        f"本节的表格必须包含以下列（按顺序）：{'、'.join(cols)}\n"
                        f"禁止增减列、禁止调换列顺序。"
                    )
            elif child_type == "fixed_form":
                segments = child.get("fixed_text_segments", [])
                if segments:
                    parts.append(
                        f"\n【招标文件规定的固定格式 — 加粗部分必须原样使用】"
                    )
                    for seg in segments:
                        if seg.get("editable") is False:
                            parts.append(f"固定措辞（不可修改）：{seg['text']}")
                        else:
                            parts.append(f"可编辑区域：{seg['text']}")

            if child.get("signature_block"):
                sig_lines = child["signature_block"].get("lines", [])
                if sig_lines:
                    parts.append(
                        f"\n【签章要求 — 必须包含以下签章行】\n" +
                        "\n".join(sig_lines)
                    )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 3. generate_chapter
# ---------------------------------------------------------------------------

async def generate_chapter(
    chapter_title: str,
    requirements: dict,
    context: str = "",
    stream: bool = True,
    format_template: dict | None = None,
) -> AsyncIterator[str] | str:
    """Generate content for a single bid document chapter.

    Builds a prompt from the chapter title, a summary of the parsed
    requirements, and any additional context (e.g. matched qualifications,
    personnel info, historical chapters).

    Args:
        chapter_title: The title of the chapter to generate.
        requirements: Parsed requirements dict from parse_bid_requirements().
        context: Additional context string (de-identified if containing PII).
        stream: If True, returns an AsyncIterator[str] for token-by-token
                streaming. If False, returns the complete response as a str.
        format_template: Optional format template dict extracted from the
            tender document for injecting format constraints.

    Returns:
        AsyncIterator[str] when stream=True; str when stream=False.
    """
    req_summary = _requirements_summary(requirements)

    context_block = ""
    if context:
        context_block = f"\n\n可供参考的资料：\n{context}"

    # ── Section-specific content guidance ──
    # Without this, the AI tends to put commitment letters (承诺书) in
    # 资格审查部分 and duplicate them in 其他内容, while the actual
    # qualification certificates end up buried in attachments.
    section_guidance = _get_section_guidance(chapter_title, format_template)

    user_prompt = f"""请撰写标书章节内容。

章节名称：{chapter_title}

招标要求摘要：
{req_summary}{context_block}

{section_guidance}

要求：
1. 内容必须针对上述招标要求作出实质性回应
2. 使用地道的中文标书行业表达
3. 每个段落至少包含1个具体事实
4. 禁止使用空泛的形容词和套话
5. 使用标题层级组织内容：章节内节标题用 ## 开头（如"## 一、服务方案"），小节标题用 ### 开头（如"### 1. 日常安保措施"）。标题将自动渲染为Word多级标题并收录到目录中。不要在内容中使用单个 #
6. 表格数据使用管道表格（| 列1 | 列2 |）或分号分隔键值对格式
7. 段落之间用空行分隔
8. 如果上文提供了【公司基本信息】，其中所有数据（公司名称、法定代表人、统一社会信用代码等）必须原封不动使用，严禁编造任何替代信息"""

    messages = _build_messages(user_prompt, format_template=format_template)

    if stream:
        return ai_adapter.chat_completion_stream(
            messages=messages,
            temperature=0.7,
        )
    else:
        return await ai_adapter.chat_completion(
            messages=messages,
            temperature=0.7,
        )


# ---------------------------------------------------------------------------
# 4. generate_chapter_with_materials
# ---------------------------------------------------------------------------

async def generate_chapter_with_materials(
    chapter_title: str,
    requirements: dict,
    matched_qualifications: list | None = None,
    matched_personnel: list | None = None,
    matched_contracts: list | None = None,
    similar_chapters: list[str] | None = None,
    company_profile: dict | None = None,
    format_template: dict | None = None,
) -> AsyncIterator[str]:
    """Generate a chapter enriched with matched company resources.

    Assembles a rich context string from:
      - Company profile (name, legal rep, business license, address, etc.)
        → injected verbatim with strict anti-fabrication instructions
      - Available qualification certificates (name + cert_number)
      - Personnel profiles (de-identified: names and ID numbers replaced
        with placeholders before entering the AI prompt)
      - Similar historical chapters for style and content reference

    Always streams output token-by-token.

    Args:
        chapter_title: The title of the chapter to generate.
        requirements: Parsed requirements dict from parse_bid_requirements().
        matched_qualifications: List of Qualification objects or dicts
            containing at least 'name' and 'cert_number'.
        matched_personnel: List of Personnel objects or dicts containing
            at least 'name', 'id_card', 'education', 'tags'. PII fields
            are de-identified before prompt assembly.
        similar_chapters: List of previously written chapter texts for
            style / content reference.
        company_profile: Optional dict with company info fields (company_name,
            business_license_number, legal_rep_name, address, etc.).
        format_template: Optional format template dict from tender document.

    Yields:
        Generated chapter text chunks as they arrive from the model.
    """
    context_parts: List[str] = []

    # --- Company profile (injected FIRST with highest priority) ---
    if company_profile:
        company_block = build_company_info_block(company_profile)
        if company_block:
            context_parts.append(company_block)

    # --- Qualifications ---
    if matched_qualifications:
        qual_lines: List[str] = ["可用资质证书："]
        for q in matched_qualifications:
            if isinstance(q, dict):
                name = q.get("name", "")
                cert = q.get("cert_number", "")
            else:
                name = getattr(q, "name", "")
                cert = getattr(q, "cert_number", "")
            qual_lines.append(f"  - {name}（证书编号：{cert}）")
        context_parts.append("\n".join(qual_lines))

    # --- Personnel (de-identified) ---
    if matched_personnel:
        personnel_lines: List[str] = ["可用项目人员："]
        for p in matched_personnel:
            if isinstance(p, dict):
                name = p.get("name", "")
                id_card = p.get("id_card", "")
                education = p.get("education", "")
                tags = p.get("tags", "")
            else:
                name = getattr(p, "name", "")
                id_card = getattr(p, "id_card", "")
                education = getattr(p, "education", "")
                tags = getattr(p, "tags", "")

            # De-identify name and ID card before prompt assembly
            safe_name, _ = deidentify_text(name) if name else ("", {})
            safe_id, _ = deidentify_text(id_card) if id_card else ("", {})
            display_name = safe_name if safe_name else name
            display_id = safe_id if safe_id else id_card

            line = f"  - {display_name}，学历{education}，持证/特长：{tags}"
            if display_id:
                line += f"，证件号：{display_id}"
            personnel_lines.append(line)

        # Also de-identify the assembled personnel block as a whole for safety
        combined = "\n".join(personnel_lines)
        safe_combined, _ = deidentify_text(combined)
        context_parts.append(safe_combined)

    # --- Contracts (for 业绩-related chapters) ---
    if matched_contracts:
        contract_lines: List[str] = ["【历史合同业绩数据 — 以下为真实项目数据，必须在标书中原样使用，严禁编造项目名称、金额等信息】"]
        for i, c in enumerate(matched_contracts, 1):
            name = c.get("project_name", "")
            if not name:
                continue
            contract_lines.append(f"{i}. 项目名称：{name}")
            if c.get("procurement_unit"):
                contract_lines.append(f"   采购单位：{c['procurement_unit']}")
            if c.get("contract_amount"):
                contract_lines.append(f"   合同金额：{c['contract_amount']}")
            if c.get("contract_date"):
                contract_lines.append(f"   合同日期：{c['contract_date']}")
            notes = c.get("notes", "")
            if notes:
                contract_lines.append(f"   备注：{notes[:200]}")
            contract_lines.append("")
        contract_lines.append("重要提醒：标书中涉及项目业绩时，必须使用以上真实项目数据，不得自行编造任何项目名称、金额等信息。")
        context_parts.append("\n".join(contract_lines))

    # --- Similar historical chapters ---
    if similar_chapters:
        history_lines: List[str] = ["历史相似章节参考（从资源库中检索到的过往标书内容，仅供参考风格和措辞）："]
        for i, chapter_text in enumerate(similar_chapters, 1):
            # Truncate each reference chapter to keep context manageable
            truncated = chapter_text[:settings.GENERATION_REF_MAX_CHARS_PER_SOURCE]
            history_lines.append(f"--- 参考章节 {i} ---\n{truncated}")
        context_parts.append("\n".join(history_lines))

    context = "\n\n".join(context_parts)

    stream_result = await generate_chapter(
        chapter_title=chapter_title,
        requirements=requirements,
        context=context,
        stream=True,
        format_template=format_template,
    )

    async for chunk in stream_result:
        yield chunk


# ---------------------------------------------------------------------------
# Generation state helpers
# ---------------------------------------------------------------------------

def _reconstruct_tree_from_leaves(leaves: list) -> list:
    """Reconstruct a nested outline tree from a flat list of leaf sections.

    Each leaf has a ``path`` (list of titles from root to leaf) plus
    budget metadata. This rebuilds the tree structure that
    ``build_final_chapters_payload`` expects, so resume mode can skip
    outline regeneration.
    """
    tree: list = []
    # Index: (depth, title) -> node dict
    node_index: dict = {}

    for leaf in leaves:
        path = leaf.get("path", [])
        if not path:
            continue

        # Ensure all ancestors exist in the tree
        for depth, title in enumerate(path):
            key = (depth, title)
            if key not in node_index:
                node = {
                    "title": title,
                    "depth": depth,
                    "children": [],
                    "max_tokens": leaf.get("max_tokens", 4096),
                    "estimated_pages": leaf.get("estimated_pages", 1),
                    "category_key": leaf.get("category_key", "medium"),
                }
                node_index[key] = node
                if depth == 0:
                    node["order_index"] = len(tree) + 1
                    tree.append(node)
                else:
                    parent_key = (depth - 1, path[depth - 1])
                    parent = node_index.get(parent_key)
                    if parent and node not in parent["children"]:
                        parent["children"].append(node)

        # Mark leaf node (no children)
        leaf_key = (len(path) - 1, path[-1])
        leaf_node = node_index.get(leaf_key)
        if leaf_node and not leaf.get("_is_placeholder"):
            # Copy budget details from stored leaf
            leaf_node["max_tokens"] = leaf.get("max_tokens", leaf_node.get("max_tokens", 4096))
            leaf_node["estimated_pages"] = leaf.get("estimated_pages", leaf_node.get("estimated_pages", 1))
            leaf_node["category_key"] = leaf.get("category_key", leaf_node.get("category_key", "medium"))

    return tree


def _init_generation_state(leaves: list) -> dict:
    """Initialize generation_state_json from flattened leaf list."""
    sections: Dict[str, dict] = {}
    for leaf in leaves:
        path_key = " > ".join(leaf.get("path", []))
        sections[path_key] = {
            "status": "pending",
            "content": None,
            "char_count": 0,
            "retries": 0,
            "error": None,
            "generated_at": None,
        }
    return {
        "status": "generating",
        "total_leaves": len(leaves),
        "completed_leaves": 0,
        "sections": sections,
        "leaves": leaves,  # store for resume — avoids regenerating outline
    }


def _update_generation_state(
    state: dict,
    path_key: str,
    status: str,
    content: str | None = None,
    error: str | None = None,
    retries: int = 0,
):
    """Update a single section's state in generation_state_json."""
    if path_key not in state["sections"]:
        state["sections"][path_key] = {}
    sec = state["sections"][path_key]
    sec["status"] = status
    if content is not None:
        sec["content"] = content
        sec["char_count"] = len(content)
        sec["generated_at"] = datetime.datetime.now().isoformat()
    if error is not None:
        sec["error"] = error
        sec["retries"] = retries

    # Recalculate completed count
    completed = sum(
        1 for s in state["sections"].values()
        if s.get("status") == "done"
    )
    state["completed_leaves"] = completed
    if completed >= state["total_leaves"]:
        state["status"] = "completed"


def _format_contract_context(contracts: list) -> str:
    """Format collected contract dicts into AI prompt context.

    Same output format as ``_gather_contract_data`` but operates on
    already-fetched collected resources (dicts) instead of querying the DB.

    Args:
        contracts: List of contract dicts from ``get_collected_resources``,
            each containing project_name, procurement_unit, contract_amount,
            service_period, contract_date, notes.

    Returns:
        Formatted text block, or empty string if list is empty.
    """
    if not contracts:
        return ""

    lines = ["【历史合同业绩数据 — 以下为真实项目数据，必须在标书中原样使用，严禁编造项目名称、金额等信息】"]
    for i, c in enumerate(contracts, 1):
        name = c.get("project_name", "")
        if not name:
            continue
        lines.append(f"{i}. 项目名称：{name}")
        if c.get("procurement_unit"):
            lines.append(f"   采购单位：{c['procurement_unit']}")
        if c.get("contract_amount"):
            lines.append(f"   合同金额：{c['contract_amount']}")
        if c.get("contract_date"):
            lines.append(f"   合同日期：{c['contract_date']}")
        notes = c.get("notes", "")
        if notes:
            lines.append(f"   备注：{notes[:200]}")
        lines.append("")

    lines.append("重要提醒：标书中涉及项目业绩时，必须使用以上真实项目数据，不得自行编造任何项目名称、金额等信息。")
    return "\n".join(lines)


async def _gather_contract_data(db) -> str:
    """Gather historical contract data for injection into AI prompt context.

    Returns formatted text block with real project data, or empty string if no data.
    """
    try:
        from app.models.contract import Contract
        from sqlalchemy import select as sa_select

        result = await db.execute(
            sa_select(Contract).order_by(Contract.created_at.desc())
        )
        contracts = result.scalars().all()

        if not contracts:
            return ""

        lines = ["【历史合同业绩数据 — 以下为真实项目数据，必须在标书中原样使用，严禁编造项目名称、金额等信息】"]
        for i, c in enumerate(contracts, 1):
            lines.append(f"{i}. 项目名称：{c.project_name}")
            if c.procurement_unit:
                lines.append(f"   采购单位：{c.procurement_unit}")
            if c.contract_amount:
                lines.append(f"   合同金额：{c.contract_amount}")
            if c.service_period:
                lines.append(f"   服务期限：{c.service_period}")
            if c.notes:
                lines.append(f"   备注：{c.notes[:200]}")
            lines.append("")

        lines.append("重要提醒：标书中涉及项目业绩时，必须使用以上真实项目数据，不得自行编造任何项目名称、金额等信息。")
        return "\n".join(lines)
    except Exception as exc:
        logger.warning("Failed to gather contract data: %s", exc)
        return ""


async def _generate_single_section_with_retry(
    leaf: dict,
    requirements: dict,
    company_profile: dict | None,
    reference_sections: list,
    max_retries: int = 2,
    retry_delay_base: float = 1.0,
    extra_guidance: str = "",
    format_template: dict | None = None,
) -> tuple:
    """Generate a single leaf section with retry.

    Returns:
        (content, error): content is None on failure; error is None on success.
    """
    from app.services.subsection_generator import generate_section

    title = leaf.get("title", "")
    path = leaf.get("path", [])
    depth = leaf.get("depth", 0)
    max_tokens = leaf.get("max_tokens", 4096)
    # 篇幅目标与推理 headroom 是两件事：max_tokens 只保证推理写完还有地方
    # 写正文，写多长由 target_chars 决定（见 token_budget.assign_target_budgets）
    target_chars = leaf.get("target_chars")
    sibling_summaries = leaf.get("sibling_summaries", [])

    # Build section-specific format guidance from tender template
    fmt_guidance = _build_section_format_guidance(title, path, format_template)
    combined_guidance = extra_guidance
    if fmt_guidance:
        if combined_guidance:
            combined_guidance = combined_guidance + "\n" + fmt_guidance
        else:
            combined_guidance = fmt_guidance

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            full_content = ""
            async for chunk in generate_section(
                section_title=title,
                section_path=path,
                depth=depth,
                requirements=requirements,
                max_tokens=max_tokens,
                target_chars=target_chars,
                sibling_summaries=sibling_summaries[:8],
                reference_sections=reference_sections,
                company_profile=company_profile,
                extra_guidance=combined_guidance,
                format_template=format_template,
            ):
                full_content += chunk

            if not full_content.strip():
                raise ValueError("AI returned empty content")

            return full_content, None

        except Exception as exc:
            last_error = str(exc)
            logger.warning(
                "Section '%s' attempt %d/%d failed: %s",
                title, attempt + 1, max_retries + 1, last_error,
            )
            if attempt < max_retries:
                delay = retry_delay_base * (2 ** attempt)
                await asyncio.sleep(delay)

    return None, last_error


# ---------------------------------------------------------------------------
# 5. generate_bid_with_deep_outline — Sequential per-section generation
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 5b. generate_from_chapter_structure — 基于锁定章节结构生成
# ---------------------------------------------------------------------------

def _is_leaf_done(node: dict) -> bool:
    """叶子是否已生成完毕（续传时跳过）."""
    return bool(node.get("content")) and node.get("status") == "generated"


def _mark_leaf_failure(tree: list, path: list[str], error: str) -> None:
    """把叶子节点标记为失败（兼容嵌套树与扁平任务列表）."""
    def _find(nodes, remaining):
        for node in nodes:
            if node.get("title") == remaining[0]:
                if len(remaining) == 1:
                    node["status"] = "failed"
                    node["error"] = error
                    return True
                kids = node.get("children") or []
                if _find(kids, remaining[1:]):
                    return True
        return False

    def _find_flat(nodes, remaining):
        for node in nodes:
            if node.get("path") == remaining:
                node["status"] = "failed"
                node["error"] = error
                return True
        return False

    if tree and isinstance(tree[0], dict) and "path" in tree[0]:
        _find_flat(tree, path)
    else:
        _find(tree, path)


def _filter_requirements_for_chapter(requirements: dict, chapter_title: str) -> dict:
    """按章节标题过滤招标要求，保留顶层结构.

    命中章节标题/章节关键词的要求条目保留，否则移除数组元素；
    顶层键（required_documents/required_personnel/project_name 等）必须保留。
    """
    if not requirements:
        return {}
    filtered = dict(requirements)
    for key in ("required_documents", "required_personnel"):
        items = filtered.get(key) or []
        if not isinstance(items, list):
            continue
        kept = []
        for it in items:
            if isinstance(it, dict):
                # required_personnel 条目以 role 标识岗位，required_documents 以 name 标识证件
                name = it.get("role", "") if key == "required_personnel" else it.get("name", "")
            else:
                name = str(it)
            if not name or _chapter_matches_requirement(chapter_title, name):
                kept.append(it)
        filtered[key] = kept
    return filtered


def _chapter_matches_requirement(chapter_title: str, req_name: str) -> bool:
    """粗略相关性：章节标题与要求名有共现词即认为相关。"""
    return chapter_title in req_name or req_name in chapter_title


def requirements_with_rubric(requirements: dict, rubric_json: str | None) -> dict:
    """把项目级评分指标并进 requirements（返回新 dict，不改调用方的）.

    评分指标存在 ``bid_projects.scoring_rubric_json``，而 generate 处理器只读
    ``parsed_requirements_json``。不并进来，生成期就看不到任何评分点。

    rubric_json 为空/损坏/无 items 时原样返回——未配置评分指标的项目照常生成。
    """
    if not rubric_json:
        return requirements
    try:
        rubric = json.loads(rubric_json)
    except (json.JSONDecodeError, TypeError):
        return requirements
    if not isinstance(rubric, dict) or not rubric.get("items"):
        return requirements
    return {**requirements, "scoring_rubric": rubric}


async def expand_pending_chapter_trees(
    chapters: list,
    requirements: dict,
    ai_adapter,
    target_pages: int = 2000,
) -> dict:
    """generate 第一阶段：为标题树为空的 AI 撰写章节就地展开标题树.

    只碰 ``children_json`` 为空的 ai_generated 章节。用户已在 UI 里细化过
    （``review_status`` 为 refining 且有子树）的章节一律不动——这里补的正是
    「从没人细化过」的缺口，而 ``refine_chapter_titles`` 端点需要逐章手动触发。

    就地改写 ``chapter.children_json``；失败章节保持原样（空串），由后续
    「整章当唯一叶子」的兜底接住，不丢章节。

    Returns:
        {"expanded": [{"title", "sections", "leaves"}...], "failed": [章节标题...]}
    """
    from app.services.title_refiner import count_leaves, expand_chapter_titles

    expanded: list[dict] = []
    failed: list[str] = []

    for chapter in chapters:
        if chapter.chapter_type != "ai_generated":
            continue
        if (chapter.children_json or "").strip() not in ("", "[]"):
            continue  # 已有子树（用户细化过或上次生成留下的），保持不动

        tree = await expand_chapter_titles(
            chapter_title=chapter.title,
            chapter_meta=_load_chapter_meta(chapter),
            requirements=requirements,
            ai_adapter=ai_adapter,
            target_pages=target_pages,
        )
        if tree:
            chapter.children_json = json.dumps(tree, ensure_ascii=False)
            expanded.append({
                "title": chapter.title,
                "sections": len(tree),
                "leaves": count_leaves(tree),
            })
        else:
            failed.append(chapter.title)

    return {"expanded": expanded, "failed": failed}


def _load_chapter_meta(chapter) -> dict:
    """章节元数据 JSON → dict；损坏时返回空 dict（展开按无上下文进行）."""
    try:
        return json.loads(chapter.chapter_meta_json) if chapter.chapter_meta_json else {}
    except (json.JSONDecodeError, TypeError):
        return {}


async def generate_from_chapter_structure(
    project_id: str,
    requirements: dict,
    company_profile: dict | None = None,
    matched_qualifications: list | None = None,
    matched_personnel: list | None = None,
    matched_contracts: list | None = None,
    db=None,
    progress_callback: Callable | None = None,
    target_pages: int = 2000,
) -> AsyncIterator[dict]:
    """基于用户确认锁定的章节结构生成标书.

    替代 generate_bid_with_deep_outline()。
    不生成 AI 大纲，直接使用 ProjectChapter 记录中的章节结构。

    流程：
    1. 加载已锁定的章节
    2. 文件/表格章节 → template_filler 生成
    3. AI 撰写章节 → 从嵌套目录树深度优先收集叶子任务 → 按目标页数规划篇幅 → 并行生成
    4. 树形组装（章节 → 容器 → 叶子）→ 输出
    """
    from app.services.content_assembler import build_final_chapters_payload
    from app.models.project import BidProject, ProjectChapter
    from sqlalchemy import select as sa_select

    if not db or not project_id:
        raise ValueError("db and project_id are required")

    # ── Load project with chapters ──
    result = await db.execute(
        sa_select(BidProject)
        .where(BidProject.id == project_id)
        .options(selectinload(BidProject.chapters))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise ValueError("Project not found")

    chapters = sorted(project.chapters, key=lambda c: c.order_index)
    if not chapters:
        raise ValueError("No chapters found — please lock chapter structure first")

    # fallback 路径（chapter 自身作为唯一叶子）下为每章建一个伪树，
    # 让 assemble 阶段 children_json 回写能包含 content，供 TreeEditor / 校验用。
    _chapter_fallback_trees: Dict[str, list] = {}

    # ── 加载格式模板（招标文件'投标文件格式'章节提取的结构化定义）──
    format_template = {}
    try:
        format_template = json.loads(project.format_template_json) if project.format_template_json else {}
    except json.JSONDecodeError:
        format_template = {}

    # ── Check review status ──
    # 接受 refining（用户尚未手动细化，由 ai_pipeline 走 fallback 路径生成）
    # 和 generating/generated（已锁定或正在/已生成）。
    # 仅 pending_review 才是真正「未就绪」的拒绝场景——理论上 outline/confirm
    # 后章节默认就是 refining，不会到这里。
    unrefined = [
        c for c in chapters
        if c.chapter_type == "ai_generated"
        and c.review_status not in ("refining", "generating", "generated")
    ]
    if unrefined:
        titles = ", ".join(c.title for c in unrefined[:3])
        raise ValueError(f"以下 AI 撰写章节尚未细化标题：{titles}。请先完成标题细化并锁定。")

    # 凡是 refining 的章节,把状态推进到 generating,UI 一致显示「正在生成」
    for c in chapters:
        if c.chapter_type == "ai_generated" and c.review_status == "refining":
            c.review_status = "generating"
    await db.flush()

    yield {
        "event": "status",
        "data": json.dumps({
            "phase": "init",
            "message": f"开始基于章节结构生成（共 {len(chapters)} 个章节）...",
        }, ensure_ascii=False),
    }

    # ── Generate file sections first ──
    file_chapters_output = []  # pre-generated file/table chapters
    from app.services.template_filler import (
        fill_fixed_form_section_from_template,
        generate_file_section,
    )

    for chapter in chapters:
        if chapter.chapter_type in ("fixed_form", "table"):
            chapter_meta = json.loads(chapter.chapter_meta_json) if chapter.chapter_meta_json else {}

            # Generate file section content.
            # 优先用招标文件原文模板扫描填充（保留原文措辞），
            # 当 format_section_text 不可用或扫描失败时回退到 AI 生成。
            file_content = ""
            if chapter.chapter_type == "fixed_form" and requirements.get("format_section_text"):
                try:
                    file_content = await fill_fixed_form_section_from_template(
                        section_title=chapter.title,
                        format_section_text=requirements["format_section_text"],
                        format_tables=requirements.get("format_tables", []),
                        company_profile=company_profile,
                        requirements=requirements,
                        ai_adapter=ai_adapter,
                    )
                    if file_content:
                        logger.info(
                            "Filled '%s' from tender template (preserves wording)",
                            chapter.title,
                        )
                except Exception as exc:
                    logger.warning(
                        "Scan-and-fill failed for '%s': %s; falling back to AI generation",
                        chapter.title, exc,
                    )

            if not file_content:
                try:
                    file_content = await generate_file_section(
                        section_type=chapter.title,
                        company_profile=company_profile,
                        requirements=requirements,
                        project_name=requirements.get("project_name", "") if requirements else "",
                        ai_adapter=ai_adapter,
                    )
                except Exception as exc:
                    logger.warning("File section '%s' generation failed: %s", chapter.title, exc)

            file_chapters_output.append({
                "id": chapter.id,
                "title": chapter.title,
                "order_index": chapter.order_index,
                "chapter_type": chapter.chapter_type,
                "content": file_content,
                "section_type": "file",
            })
            yield {
                "event": "section_done",
                "data": json.dumps({
                    "path": chapter.title,
                    "title": chapter.title,
                    "content": file_content,
                    "content_length": len(file_content),
                    "index": chapter.order_index,
                    "total": len(chapters),
                }, ensure_ascii=False),
            }

    # ── 第一阶段：为从未细化过的 AI 章节展开标题树 ──
    # 放在这里而不是 outline/confirm：展开要 1-3 分钟，前端 axios 超时 120s。
    # 只补 children_json 为空的章节；用户手工细化过的树保持不动。
    pending = [
        c for c in chapters
        if c.chapter_type == "ai_generated"
        and (c.children_json or "").strip() in ("", "[]")
    ]
    if pending:
        yield {
            "event": "status",
            "data": json.dumps({
                "phase": "expanding",
                "message": f"正在展开 {len(pending)} 个章节的标题结构...",
            }, ensure_ascii=False),
        }
        expand_report = await expand_pending_chapter_trees(
            chapters, requirements, ai_adapter, target_pages)
        for item in expand_report["expanded"]:
            yield {
                "event": "chapter_expanded",
                "data": json.dumps(item, ensure_ascii=False),
            }
        for title in expand_report["failed"]:
            # 展开失败的章节不丢：后续「整章当唯一叶子」的兜底照常接手
            logger.warning("章节标题展开失败，按整章单叶子生成：%s", title)
            yield {
                "event": "chapter_expand_failed",
                "data": json.dumps({"title": title}, ensure_ascii=False),
            }
        await db.flush()

    # ── Collect all leaf tasks from ai_generated chapters（嵌套树，文档顺序）──
    all_tasks = []

    def _collect_leaf_tasks(
        nodes: list,
        chapter_id: str,
        chapter_title: str,
        parent_path: list,
    ) -> None:
        """深度优先收集叶子任务；兼容嵌套树与旧扁平任务列表两种 children_json。"""
        for node in nodes:
            title = node.get("title", "")
            if node.get("path"):
                # 旧扁平任务格式：path 已含完整祖先链，直接使用
                node_path = node["path"]
            else:
                node_path = parent_path + [title]
            kids = node.get("children", []) or []
            if kids:
                _collect_leaf_tasks(kids, chapter_id, chapter_title, node_path)
            else:
                all_tasks.append({
                    "chapter_id": chapter_id,
                    "chapter_title": chapter_title,
                    "task": {
                        "path": node_path,
                        "title": title,
                        "depth": node.get("depth", len(node_path) - 1),
                        "token_budget_hint": node.get("token_budget_hint", "medium"),
                    },
                })

    for chapter in chapters:
        if chapter.chapter_type == "ai_generated":
            # Load children tree
            try:
                children = json.loads(chapter.children_json) if chapter.children_json else []
            except json.JSONDecodeError:
                children = []

            if not children:
                # Fallback: treat the chapter itself as one generation task
                all_tasks.append({
                    "chapter_id": chapter.id,
                    "chapter_title": chapter.title,
                    "task": {
                        "path": [chapter.title],
                        "title": chapter.title,
                        "depth": 0,
                        "token_budget_hint": "large",
                    },
                })
                # 同步建一个伪树（单叶子），让 assemble 阶段回写时能找到 children_json
                _chapter_fallback_trees[chapter.id] = [{
                    "title": chapter.title,
                    "depth": 0,
                    "token_budget_hint": "large",
                    "path": [chapter.title],
                    "_is_fallback_leaf": True,
                }]
            else:
                _collect_leaf_tasks(children, chapter.id, chapter.title, [chapter.title])

    total_leaves = len(all_tasks)

    # ── 按目标页数规划每个叶子的篇幅 ──
    from app.services.token_budget import assign_target_budgets
    assign_target_budgets([t["task"] for t in all_tasks], target_pages)

    estimated_pages = sum(
        t["task"].get("estimated_pages", 1) for t in all_tasks
    )

    yield {
        "event": "outline_generated",
        "data": json.dumps({
            "total_leaves": total_leaves,
            "estimated_pages": estimated_pages,
            "target_pages": target_pages,
            "max_depth": 4,
            "completed_from_previous": 0,
        }, ensure_ascii=False),
    }

    # ── Phase: 逐章节串行生成（每章上下文只装本章需要的，章内叶子并行）──
    parallel_workers = max(1, settings.GENERATION_PARALLEL_SECTIONS)

    yield {
        "event": "status",
        "data": json.dumps({
            "phase": "generating",
            "message": f"开始逐章生成（每章内部 {parallel_workers} 路并发），"
                       f"目标 {target_pages} 页，预计约 {estimated_pages} 页...",
            "total_leaf_sections": total_leaves,
            "completed_leaf_sections": 0,
        }, ensure_ascii=False),
    }

    generated_sections: Dict[str, str] = {}
    completed = 0
    chapter_errors: list[str] = []
    chapters_payload: list[dict] = []
    lead_ins: Dict[str, str] = {}

    from app.services.content_assembler import generate_chapter_summary
    from app.services.subsection_generator import generate_container_lead_in

    # 解析每章 children_json 为树；原样保留 content/status/error（续传判定依赖），
    # 兼容嵌套树与旧扁平任务列表（扁平 = 首元素含 "path" 键）。
    def _load_chapter_tree(chapter) -> list:
        try:
            nodes = json.loads(chapter.children_json) if chapter.children_json else []
        except json.JSONDecodeError:
            nodes = []
        if not isinstance(nodes, list) or not nodes:
            return []
        return nodes

    def _locate_leaf(nodes: list, full_path: list) -> dict | None:
        """在章节树中按完整路径（含章节标题）定位叶子节点."""
        if not nodes or not full_path:
            return None
        if isinstance(nodes[0], dict) and "path" in nodes[0]:
            for node in nodes:
                if node.get("path") == full_path:
                    return node
            return None
        remaining = full_path[1:]  # 嵌套树根不含章节标题
        cur = nodes
        for i, title in enumerate(remaining):
            match = next((n for n in cur if n.get("title") == title), None)
            if match is None:
                return None
            if i == len(remaining) - 1:
                return match
            cur = match.get("children") or []
        return None

    def _build_child_summaries(container_path: list, children: list) -> list[str]:
        summaries = []
        for child in children:
            child_title = child.get("title", "")
            if child.get("children"):
                summaries.append(f"{child_title}（含 {len(child['children'])} 个子章节）")
            else:
                child_path = child.get("path") or (container_path + [child_title])
                content = generated_sections.get(" > ".join(child_path), "")
                if content:
                    summaries.append(f"{child_title}：{generate_chapter_summary(content)}")
                else:
                    summaries.append(child_title)
        return summaries

    title_to_order = {c.title: c.order_index for c in chapters}

    # 只有 ai_generated 章节会 emit chapter_start，index/total 应只统计 AI 章节，
    # 否则会把文件/表格章节也计入 total（且 index 出现跳号）。
    ai_chapters = [c for c in chapters if c.chapter_type == "ai_generated"]

    for ai_index, chapter in enumerate(ai_chapters, start=1):
        yield {
            "event": "chapter_start",
            "data": json.dumps({
                "chapter_id": chapter.id, "title": chapter.title,
                "index": ai_index, "total": len(ai_chapters),
            }, ensure_ascii=False),
        }

        # 本章素材上下文（四类，按标题关键词）
        materials_guidance = assemble_section_materials(
            chapter.title,
            qualifications=matched_qualifications,
            personnel=matched_personnel,
            contracts=matched_contracts,
            company=company_profile,
        )
        # 本章招标要求过滤：保留顶层键，缩小数组
        chapter_requirements = _filter_requirements_for_chapter(
            requirements, chapter.title
        )

        # 本章叶子任务 + 本章树
        chapter_tasks = [t for t in all_tasks if t["chapter_id"] == chapter.id]
        children_tree = _chapter_fallback_trees.get(chapter.id) or _load_chapter_tree(chapter)

        leaf_failed = 0
        leaf_done = 0
        chapter_error_msg = None
        try:
            # 续传：已生成叶子（status=generated 且有 content）跳过，复用已有 content
            pending_tasks = []
            for task_info in chapter_tasks:
                node = _locate_leaf(children_tree, task_info["task"]["path"])
                if node is not None and _is_leaf_done(node):
                    path_key = " > ".join(task_info["task"]["path"])
                    generated_sections[path_key] = node["content"]
                    leaf_done += 1
                    completed += 1
                    yield {
                        "event": "section_done",
                        "data": json.dumps({
                            "chapter_id": task_info["chapter_id"],
                            "path": path_key,
                            "section_path": task_info["task"]["path"],
                            "title": task_info["task"]["title"],
                            "content": node["content"],
                            "content_length": len(node["content"]),
                            "char_count": len(node["content"]),
                            "index": completed,
                            "total": total_leaves,
                        }, ensure_ascii=False),
                    }
                    yield {
                        "event": "progress",
                        "data": json.dumps({
                            "completed": completed,
                            "total": total_leaves,
                            "percentage": round(completed / max(total_leaves, 1) * 100, 1),
                        }, ensure_ascii=False),
                    }
                else:
                    pending_tasks.append(task_info)

            if pending_tasks:
                # 本章叶子并行生成（semaphore）
                semaphore = asyncio.Semaphore(parallel_workers)

                async def _gen_one(task_info: dict) -> dict:
                    async with semaphore:
                        task = task_info["task"]
                        path_key = " > ".join(task["path"])
                        title = task["title"]
                        depth = task.get("depth", 0)
                        max_tokens = task.get("max_tokens") or _budget_hint_to_tokens(task.get("token_budget_hint", "medium"))

                        # Build section guidance + 招标文件格式约束 + 本章素材
                        from app.services.ai_pipeline import _get_section_guidance, _build_section_format_guidance
                        guidance = _get_section_guidance(title, format_template)
                        guidance += _build_section_format_guidance(title, task["path"], format_template)
                        if materials_guidance:
                            guidance += "\n\n【可用的真实素材（标书中必须使用，严禁编造）】\n" + materials_guidance

                        # 复用带指数退避的重试版（原来这里自己写了一版且**不重试**，
                        # 空返回直接记 empty_content 收工）。实测空返回基本是暂态的：
                        # 重跑两轮 44→18→8，每轮捞回约 56-60% 的剩余失败。
                        # format_template 传 None —— guidance 里已经拼过
                        # _build_section_format_guidance，再传一次会重复注入。
                        full_content, err = await _generate_single_section_with_retry(
                            leaf=task,
                            requirements=chapter_requirements,
                            company_profile=company_profile,
                            reference_sections=[],
                            max_retries=2,
                            retry_delay_base=1.0,
                            extra_guidance=guidance,
                            format_template=None,
                        )
                        if err is not None:
                            logger.error("Section '%s' generation failed: %s", title, err)
                            return {
                                "chapter_id": task_info["chapter_id"],
                                "path_key": path_key,
                                "section_path": task["path"],
                                "title": title,
                                "content": None,
                                "error": err,
                            }

                        # 预算耗尽时 AI 可能以孤立标题行收尾，裁掉避免"空标题"
                        full_content = _strip_trailing_headings(full_content)
                        if not full_content or not full_content.strip():
                            return {
                                "chapter_id": task_info["chapter_id"],
                                "path_key": path_key,
                                "section_path": task["path"],
                                "title": title,
                                "content": None,
                                "error": "empty_content",
                            }
                        return {
                            "chapter_id": task_info["chapter_id"],
                            "path_key": path_key,
                            "section_path": task["path"],
                            "title": title,
                            "content": full_content,
                            "error": None,
                        }

                # Emit section_start for pending tasks（index 用全局单调计数，跨章不重置）
                for i, ti in enumerate(pending_tasks, start=completed + 1):
                    task_path = ti["task"]["path"]
                    yield {
                        "event": "section_start",
                        "data": json.dumps({
                            "chapter_id": ti["chapter_id"],
                            "path": " > ".join(task_path),
                            "section_path": task_path,  # for frontend tree navigation
                            "title": ti["task"]["title"],
                            "index": i,
                            "total": total_leaves,
                            "depth": ti["task"].get("depth", 0),
                        }, ensure_ascii=False),
                    }

                tasks_coros = [asyncio.create_task(_gen_one(ti)) for ti in pending_tasks]
                for coro in asyncio.as_completed(tasks_coros):
                    result = await coro
                    path_key = result["path_key"]
                    content = result["content"]
                    completed += 1

                    if content:
                        generated_sections[path_key] = content
                        leaf_done += 1
                        node = _locate_leaf(children_tree, result["section_path"])
                        if node is not None:
                            node["content"] = content
                            node["status"] = "generated"
                        yield {
                            "event": "section_done",
                            "data": json.dumps({
                                "chapter_id": result["chapter_id"],
                                "path": path_key,
                                "section_path": result.get("section_path", []),
                                "title": result["title"],
                                "content": content,
                                "content_length": len(content),
                                "char_count": len(content),
                                "index": completed,
                                "total": total_leaves,
                            }, ensure_ascii=False),
                        }
                    else:
                        leaf_failed += 1
                        node = _locate_leaf(children_tree, result["section_path"])
                        if node is None:
                            logger.warning(
                                "Leaf not located for failure-marking (section '%s', path %s)",
                                result["title"], " > ".join(result["section_path"]),
                            )
                        else:
                            mark_path = result["section_path"]
                            if not (children_tree and isinstance(children_tree[0], dict) and "path" in children_tree[0]):
                                mark_path = mark_path[1:] if len(mark_path) > 1 else mark_path
                            _mark_leaf_failure(children_tree, mark_path, result.get("error") or "unknown")
                        yield {
                            "event": "section_error",
                            "data": json.dumps({
                                "chapter_id": result["chapter_id"],
                                "path": path_key,
                                "section_path": result.get("section_path", []),
                                "title": result["title"],
                                "error": result.get("error", "unknown"),
                                "index": completed,
                                "total": total_leaves,
                            }, ensure_ascii=False),
                        }

                    yield {
                        "event": "progress",
                        "data": json.dumps({
                            "completed": completed,
                            "total": total_leaves,
                            "percentage": round(completed / max(total_leaves, 1) * 100, 1),
                        }, ensure_ascii=False),
                    }

            # 本章容器引导段（含章节根）：用子内容摘要并行生成
            if children_tree:
                chapter_leadin_items: list[tuple] = []
                chapter_leadin_items.append((chapter.title, {"title": chapter.title, "children": children_tree}, [chapter.title]))

                def _walk(nodes, path):
                    for node in nodes:
                        node_path = path + [node["title"]]
                        if node.get("children"):
                            chapter_leadin_items.append((chapter.title, node, node_path))
                            _walk(node["children"], node_path)

                _walk(children_tree, [chapter.title])

                leadin_sem = asyncio.Semaphore(parallel_workers)

                async def _gen_leadin(item) -> tuple | None:
                    _chapter_title, node, node_path = item
                    key = " > ".join(node_path)
                    if key in generated_sections:  # 防御：容器不可能是叶子
                        return None
                    summaries = _build_child_summaries(node_path, node.get("children", []))
                    async with leadin_sem:
                        text = await generate_container_lead_in(
                            container_title=node["title"],
                            section_path=node_path,
                            requirements=chapter_requirements,
                            company_profile=company_profile,
                            child_summaries=summaries,
                        )
                    return (key, text) if text else None

                leadin_results = await asyncio.gather(*(_gen_leadin(it) for it in chapter_leadin_items))
                for res in leadin_results:
                    if res:
                        lead_ins[res[0]] = res[1]

            # 本章组装（build_final_chapters_payload 按章产出后 append 到全局）
            if not children_tree:
                content = generated_sections.get(chapter.title, "")
                chapters_payload.append({
                    "title": chapter.title,
                    "content": content,
                    "order_index": title_to_order.get(chapter.title, 999),
                })
            else:
                wrapper = {
                    "title": chapter.title,
                    "depth": 0,
                    "children": children_tree,
                    "lead_in": lead_ins.get(chapter.title, ""),
                }
                payload = build_final_chapters_payload([wrapper], generated_sections)
                if payload:
                    payload[0]["order_index"] = title_to_order.get(chapter.title, 999)
                    chapters_payload.append(payload[0])

            # 回写 children_json：叶子 content + status，容器 lead_in（供 TreeEditor / 续传）
            def _writeback(nodes, path):
                for node in nodes:
                    node_path = path + [node["title"]]
                    if node.get("children"):
                        lead = lead_ins.get(" > ".join(node_path))
                        if lead:
                            node["lead_in"] = lead
                        _writeback(node["children"], node_path)
                    else:
                        leaf_path = node.get("path") or node_path
                        content = generated_sections.get(" > ".join(leaf_path), "")
                        if content:
                            node["content"] = content
                            node["status"] = "generated"

            _writeback(children_tree, [chapter.title])
            try:
                chapter.children_json = json.dumps(children_tree, ensure_ascii=False)
            except Exception:
                pass
            await db.commit()
        except Exception as exc:
            logger.exception("Chapter '%s' generation failed: %s", chapter.title, exc)
            chapter_errors.append(chapter.title)
            chapter_error_msg = str(exc)
            # 标红本章「尚未生成完成」的叶子；已生成（status=generated）或已标红的叶子保持原样
            is_flat = bool(children_tree and isinstance(children_tree[0], dict) and "path" in children_tree[0])
            for task_info in chapter_tasks:
                node = _locate_leaf(children_tree, task_info["task"]["path"])
                if node is None:
                    logger.warning(
                        "Chapter '%s' leaf not located for failure-marking: %s",
                        chapter.title, " > ".join(task_info["task"]["path"]),
                    )
                    continue
                if _is_leaf_done(node) or node.get("status") == "failed":
                    continue
                mark_path = task_info["task"]["path"]
                if not is_flat:
                    mark_path = mark_path[1:] if len(mark_path) > 1 else mark_path
                _mark_leaf_failure(children_tree, mark_path, str(exc))
                leaf_failed += 1
            try:
                chapter.children_json = json.dumps(children_tree, ensure_ascii=False)
                await db.commit()
            except Exception:
                pass

        yield {
            "event": "chapter_error" if chapter_error_msg else "chapter_done",
            "data": json.dumps({
                "chapter_id": chapter.id, "title": chapter.title,
                "leaf_success": leaf_done, "leaf_failed": leaf_failed,
                "error": chapter_error_msg,
            }, ensure_ascii=False),
        }

    if chapter_errors:
        logger.warning("Some chapters failed generation: %s", ", ".join(chapter_errors))

    # ── Phase: 合并章节（树形组装已在逐章循环内完成）──
    yield {
        "event": "status",
        "data": json.dumps({
            "phase": "assembling",
            "message": "正在生成分组引导段并组装章节内容...",
        }, ensure_ascii=False),
    }

    # 文件/表格章节（保持在前，按 order_index 排序）
    for fc in file_chapters_output:
        chapters_payload.append({
            "title": fc["title"],
            "content": fc.get("content", ""),
            "section_type": fc.get("section_type", "file"),
            "order_index": title_to_order.get(fc["title"], 999),
        })

    chapters_payload.sort(key=lambda c: c.get("order_index", 999))

    # ── Phase: 格式校验（与招标文件格式模板对账）──
    from app.services.format_verifier import verify_format
    verification = verify_format(chapters_payload, format_template)
    try:
        project.format_verification_json = json.dumps(verification, ensure_ascii=False)
        await db.commit()
    except Exception:
        pass
    yield {
        "event": "format_verification",
        "data": json.dumps(verification, ensure_ascii=False),
    }

    # ── Phase: 自我评分（评标办法驱动，不阻塞 done）──
    try:
        rubric = json.loads(getattr(project, "scoring_rubric_json", "{}") or "{}")
    except json.JSONDecodeError:
        rubric = {}
    if rubric.get("status") in ("found", "manual") and rubric.get("items"):
        try:
            from app.services.score_engine import run_scoring
            report = await run_scoring(rubric, chapters_payload, ai_adapter)
            try:
                project.scoring_report_json = json.dumps(report, ensure_ascii=False)
                await db.commit()
            except Exception:
                pass
            yield {
                "event": "scoring_report",
                "data": json.dumps(report, ensure_ascii=False),
            }
        except Exception as exc:
            logger.warning("自我评分失败（不阻塞 done）: %s", exc)

    # ── Phase: Done ──
    yield {
        "event": "done",
        "data": json.dumps({
            "chapters_count": len(chapters_payload),
            "total_chars": sum(len(c.get("content", "")) for c in chapters_payload),
            "completed_sections": completed,
            "total_sections": total_leaves,
            "chapters": chapters_payload,
        }, ensure_ascii=False),
    }


def _budget_hint_to_tokens(hint: str) -> int:
    """Convert a token budget hint to actual token count."""
    from app.services.token_budget import hint_to_tokens
    return hint_to_tokens(hint)


# ---------------------------------------------------------------------------
# 5. generate_bid_with_deep_outline — (legacy, kept for backward compatibility)
# ---------------------------------------------------------------------------

async def generate_bid_with_deep_outline(
    requirements: dict,
    company_profile: dict | None = None,
    matched_qualifications: list | None = None,
    matched_personnel: list | None = None,
    matched_contracts: list | None = None,
    project_id: str = "",
    db=None,
    progress_callback: Callable | None = None,
    resume: bool = False,
) -> AsyncIterator[dict]:
    """Generate a complete bid document — incremental per-section generation.

    No ThreadPoolExecutor. Each leaf section is generated sequentially in
    the main async context. Each section is persisted immediately.

    When resume=True, leaves are loaded from the stored generation_state_json
    and outline regeneration (Phase 1+2) is skipped.
    """
    from app.services.outline_engine import generate_deep_outline
    from app.services.subsection_generator import prepare_outline_tree, get_outline_stats
    from app.services.content_assembler import build_final_chapters_payload
    from app.services.reference_analyzer import get_reference_outlines
    from app.services.rag import retrieve_lesson_references, retrieve_similar_chapters
    from app.services.token_budget import collect_leaf_sections
    from app.models.project import BidProject
    from sqlalchemy import select as sa_select

    # ── Resume path: load leaves from stored generation_state ──
    tree = None
    stats = None
    leaves = None

    if resume:
        if not db or not project_id:
            raise ValueError("resume=True requires db and project_id")
        result = await db.execute(
            sa_select(BidProject).where(BidProject.id == project_id)
        )
        db_project = result.scalar_one_or_none()
        if not db_project or not db_project.generation_state_json:
            raise ValueError("No generation state found — cannot resume")
        prev_state = json.loads(db_project.generation_state_json)
        stored_leaves = prev_state.get("leaves")
        if not stored_leaves:
            raise ValueError("Generation state missing leaves data — cannot resume")
        leaves = stored_leaves
        # Reconstruct tree from leaves for assembly
        tree = _reconstruct_tree_from_leaves(leaves)
        stats = get_outline_stats(tree) if tree else {"total_leaf_sections": len(leaves), "estimated_pages": 0, "max_depth": 1}
        logger.info("Resume mode: loaded %d leaves from stored state", len(leaves))

    # ── Load format_template from DB (best-effort, non-blocking) ──
    fmt_template: dict | None = None
    if db and project_id:
        try:
            result = await db.execute(
                sa_select(BidProject).where(BidProject.id == project_id)
            )
            db_proj = result.scalar_one_or_none()
            if db_proj and db_proj.format_template_json and db_proj.format_template_json != "{}":
                fmt_template = json.loads(db_proj.format_template_json)
                logger.info("Loaded format_template for project %s", project_id)
        except Exception as exc:
            logger.debug("Failed to load format_template: %s", exc)

    # ── Phase 1: Build reference outlines (skip on resume) ──
    if not resume:
        reference_outlines = []
        if db:
            try:
                reference_outlines = await get_reference_outlines(db)
            except Exception as exc:
                logger.warning("Failed to load reference outlines: %s", exc)

        # ── Phase 2: Generate deep outline ──
        yield {
            "event": "status",
            "data": json.dumps({
                "phase": "outline",
                "message": "正在生成深度大纲结构...",
            }, ensure_ascii=False),
        }

        tender_text = ""
        try:
            if requirements:
                tender_text = json.dumps(requirements, ensure_ascii=False)
        except Exception:
            pass

        deep_outline = await generate_deep_outline(
            requirements=requirements,
            reference_outlines=reference_outlines,
            tender_text=tender_text,
            min_leaves=settings.GENERATION_MIN_LEAF_SECTIONS,
            max_leaves=settings.GENERATION_MAX_LEAF_SECTIONS,
            format_template=fmt_template,
        )

        tree = prepare_outline_tree(deep_outline, requirements)
        stats = get_outline_stats(tree)
        leaves = collect_leaf_sections(tree)

    # ── Initialize generation state ──
    gen_state = _init_generation_state(leaves)

    # ── Restore previously completed sections (resume support) ──
    if db:
        try:
            from sqlalchemy import select as sa_select
            result = await db.execute(
                sa_select(BidProject).where(BidProject.id == project_id)
            )
            db_project = result.scalar_one_or_none()
            if db_project and db_project.generation_state_json:
                try:
                    prev_state = json.loads(db_project.generation_state_json)
                    if prev_state.get("sections"):
                        for path_key, sec in prev_state["sections"].items():
                            if sec.get("status") == "done" and sec.get("content"):
                                if path_key in gen_state["sections"]:
                                    gen_state["sections"][path_key] = sec
                        gen_state["completed_leaves"] = sum(
                            1 for s in gen_state["sections"].values()
                            if s.get("status") == "done"
                        )
                        logger.info(
                            "Restored %d completed sections from previous run",
                            gen_state["completed_leaves"],
                        )
                except Exception:
                    pass
            # Save initial state
            if db_project:
                db_project.generation_state_json = json.dumps(gen_state, ensure_ascii=False)
                await db.commit()
        except Exception as exc:
            logger.warning("Failed to load/save generation state: %s", exc)

    # ── Yield outline event ──
    yield {
        "event": "outline_generated",
        "data": json.dumps({
            "total_leaves": stats["total_leaf_sections"],
            "estimated_pages": stats["estimated_pages"],
            "max_depth": stats["max_depth"],
            "completed_from_previous": gen_state["completed_leaves"],
            "outline_tree": tree,
        }, ensure_ascii=False),
    }

    # ── Phase 2.5: 文件类章节生成 ──
    # 使用 AI 从头生成标准文件章节（投标函、承诺书、法定代表人证明等）
    # 替代原有的 tender text scan-and-fill 方案（PDF 提取乱码导致填充质量差）
    file_section_chapters: list = []  # list of {"title": str, "content": str}
    if db:
        try:
            from app.services.template_filler import generate_all_file_sections
            project_name = requirements.get("project_name", "") if requirements else ""

            generated_sections = await generate_all_file_sections(
                company_profile=company_profile,
                requirements=requirements,
                project_name=project_name,
                ai_adapter=ai_adapter,
            )
            if generated_sections:
                for section_title, content in generated_sections.items():
                    file_section_chapters.append({
                        "title": section_title,
                        "content": content,
                        "section_type": "file",
                    })
                yield {
                    "event": "status",
                    "data": json.dumps({
                        "phase": "file_section",
                        "message": f"文件类章节已生成完成（共 {len(generated_sections)} 个）",
                    }, ensure_ascii=False),
                }
        except Exception as exc:
            logger.warning("File section generation failed: %s", exc)

    # ── Phase 2.6: Gather contract + personnel data for context injection ──
    contract_context = ""
    if matched_contracts:
        # Prefer collected (user-selected) contracts over the full library
        contract_context = _format_contract_context(matched_contracts)
        if contract_context:
            logger.info("Using collected contracts context: %d chars", len(contract_context))
    elif db:
        try:
            contract_context = await _gather_contract_data(db)
            if contract_context:
                logger.info("Gathered contract data: %d chars", len(contract_context))
        except Exception as exc:
            logger.warning("Failed to gather contract data: %s", exc)

    # Build personnel context block from matched_personnel
    personnel_context = build_personnel_context(matched_personnel or [])
    if personnel_context:
        logger.info("Built personnel context: %d chars", len(personnel_context))

    # ── Phase 3: Generate sections with configurable parallelism ──
    resume_note = "（断点续传）" if resume else ""
    parallel_workers = max(1, settings.GENERATION_PARALLEL_SECTIONS)

    # Collect sibling titles per section for anti-duplication
    all_titles = [leaf.get("title", "") for leaf in leaves]
    total = len(leaves)

    # ── Identify pending sections (skip completed / max-retried) ──
    pending_sections: list = []
    for idx, leaf in enumerate(leaves):
        path_key = " > ".join(leaf.get("path", []))
        sec_state = gen_state["sections"].get(path_key, {})

        if sec_state.get("status") == "done" and sec_state.get("content"):
            logger.info("Skipping already-completed section: %s", path_key)
            continue

        if sec_state.get("status") == "failed" and sec_state.get("retries", 0) >= settings.GENERATION_MAX_RETRIES + 1:
            continue

        leaf["_idx"] = idx
        leaf["_path_key"] = path_key
        # Pre-build sibling summaries (title-only, no content dependency)
        leaf["sibling_summaries"] = [
            f"{t}（详见该章节）" for t in all_titles if t != leaf.get("title", "")
        ]
        # Inject contract context for 业绩-related sections
        title_lower = leaf.get("title", "").lower()
        extra_parts = []
        if contract_context and any(kw in title_lower for kw in CONTRACT_KEYWORDS):
            extra_parts.append(contract_context)
        if personnel_context and any(kw in title_lower for kw in PERSONNEL_CTX_KEYWORDS):
            extra_parts.append(personnel_context)
        quals_context = build_qualifications_context(matched_qualifications or [])
        if quals_context and any(kw in title_lower for kw in QUAL_CTX_KEYWORDS):
            extra_parts.append(quals_context)
        # 文件类章节已生成完毕，提醒 AI 只需撰写技术方案部分
        if file_section_chapters:
            extra_parts.append("注意：投标文件中的投标函、承诺书、法定代表人证明等文件类内容已由系统自动填充，你只需撰写技术方案部分。")
        if extra_parts:
            leaf["_extra_guidance"] = "\n\n".join(extra_parts)
        pending_sections.append(leaf)

    # ── Pre-fetch RAG references for all pending sections ──
    async def _fetch_rag(leaf: dict) -> list:
        if not db:
            return []
        try:
            async with async_session() as rag_db:
                similar = await retrieve_similar_chapters(
                    chapter_title=leaf.get("title", ""),
                    requirements=requirements,
                    project_id=project_id,
                )
                lessons = await retrieve_lesson_references(
                    requirements, project_id, n_results=3
                )
                refs = [s.get("content", "") for s in similar if s.get("content")]
                refs += [s.get("content", "") for s in lessons if s.get("content")]
                return refs
        except Exception as exc:
            logger.debug("RAG failed for '%s': %s", leaf.get("title", ""), exc)
            return []

    if pending_sections:
        rag_tasks = [asyncio.create_task(_fetch_rag(leaf)) for leaf in pending_sections]
        rag_results = await asyncio.gather(*rag_tasks)
        for leaf, refs in zip(pending_sections, rag_results):
            leaf["_reference_sections"] = refs

    # ── Status: starting generation ──
    mode_label = "并行" if parallel_workers > 1 else "逐节"
    yield {
        "event": "status",
        "data": json.dumps({
            "phase": "generating",
            "message": f"开始{mode_label}生成{resume_note}（共 {total} 个小节，已完成 {gen_state['completed_leaves']} 个，{parallel_workers} 路并发）...",
            "total_leaf_sections": total,
            "completed_leaf_sections": gen_state["completed_leaves"],
        }, ensure_ascii=False),
    }

    if not pending_sections:
        # All sections already completed — skip to assembly
        pass
    elif parallel_workers <= 1:
        # ── Serial fallback ──
        for leaf in pending_sections:
            path_key = leaf["_path_key"]
            idx = leaf["_idx"]

            yield {
                "event": "section_start",
                "data": json.dumps({
                    "path": path_key,
                    "title": leaf.get("title", ""),
                    "index": idx + 1,
                    "total": total,
                    "depth": leaf.get("depth", 0),
                    "estimated_pages": leaf.get("estimated_pages", 1),
                }, ensure_ascii=False),
            }
            yield {
                "event": "subsection_status",
                "data": json.dumps({
                    "completed": gen_state["completed_leaves"],
                    "total": total,
                    "current_title": leaf.get("title", ""),
                }, ensure_ascii=False),
            }

            _update_generation_state(gen_state, path_key, "generating")

            content, error = await _generate_single_section_with_retry(
                leaf=leaf,
                requirements=requirements,
                company_profile=company_profile,
                reference_sections=leaf.get("_reference_sections", []),
                max_retries=settings.GENERATION_MAX_RETRIES,
                retry_delay_base=settings.GENERATION_RETRY_DELAY_BASE,
                extra_guidance=leaf.get("_extra_guidance", ""),
                format_template=fmt_template,
            )

            if content:
                _update_generation_state(gen_state, path_key, "done", content=content)
                yield {
                    "event": "section_done",
                    "data": json.dumps({
                        "path": path_key,
                        "title": leaf.get("title", ""),
                        "content": content,
                        "content_length": len(content),
                        "char_count": len(content),
                        "index": idx + 1,
                        "total": total,
                    }, ensure_ascii=False),
                }
                yield {
                    "event": "subsection_chunk",
                    "data": json.dumps({
                        "chapter_id": path_key,
                        "text": content,
                    }, ensure_ascii=False),
                }
            else:
                retries = settings.GENERATION_MAX_RETRIES + 1
                _update_generation_state(
                    gen_state, path_key, "failed",
                    error=error, retries=retries,
                )
                yield {
                    "event": "section_error",
                    "data": json.dumps({
                        "path": path_key,
                        "title": leaf.get("title", ""),
                        "error": error,
                        "retry_count": retries,
                        "index": idx + 1,
                        "total": total,
                    }, ensure_ascii=False),
                }

            # Persist after every section
            if db:
                try:
                    from sqlalchemy import select as sa_select
                    result = await db.execute(
                        sa_select(BidProject).where(BidProject.id == project_id)
                    )
                    db_project = result.scalar_one_or_none()
                    if db_project:
                        db_project.generation_state_json = json.dumps(gen_state, ensure_ascii=False)
                        await db.commit()
                except Exception as exc:
                    logger.error("Failed to persist generation state: %s", exc)

            yield {
                "event": "progress",
                "data": json.dumps({
                    "completed": gen_state["completed_leaves"],
                    "total": gen_state["total_leaves"],
                    "percentage": round(gen_state["completed_leaves"] / max(gen_state["total_leaves"], 1) * 100, 1),
                }, ensure_ascii=False),
            }
    else:
        # ── Parallel generation (workers > 1) ──
        state_lock = asyncio.Lock()
        semaphore = asyncio.Semaphore(parallel_workers)

        # Emit section_start + subsection_status for all pending sections upfront
        for leaf in pending_sections:
            yield {
                "event": "section_start",
                "data": json.dumps({
                    "path": leaf["_path_key"],
                    "title": leaf.get("title", ""),
                    "index": leaf["_idx"] + 1,
                    "total": total,
                    "depth": leaf.get("depth", 0),
                    "estimated_pages": leaf.get("estimated_pages", 1),
                }, ensure_ascii=False),
            }
            yield {
                "event": "subsection_status",
                "data": json.dumps({
                    "completed": gen_state["completed_leaves"],
                    "total": total,
                    "current_title": leaf.get("title", ""),
                }, ensure_ascii=False),
            }

        async def _generate_one(leaf: dict) -> dict:
            """Generate a single section, gated by the semaphore."""
            async with semaphore:
                path_key = leaf["_path_key"]
                async with state_lock:
                    _update_generation_state(gen_state, path_key, "generating")

                content, error = await _generate_single_section_with_retry(
                    leaf=leaf,
                    requirements=requirements,
                    company_profile=company_profile,
                    reference_sections=leaf.get("_reference_sections", []),
                    max_retries=settings.GENERATION_MAX_RETRIES,
                    retry_delay_base=settings.GENERATION_RETRY_DELAY_BASE,
                    format_template=fmt_template,
                )
                return {
                    "idx": leaf["_idx"],
                    "path_key": path_key,
                    "title": leaf.get("title", ""),
                    "content": content,
                    "error": error,
                }

        tasks = [asyncio.create_task(_generate_one(leaf)) for leaf in pending_sections]

        for coro in asyncio.as_completed(tasks):
            result = await coro
            idx = result["idx"]
            path_key = result["path_key"]
            title = result["title"]
            content = result["content"]
            error = result["error"]

            # Update gen_state (serialised via lock)
            async with state_lock:
                if content:
                    _update_generation_state(gen_state, path_key, "done", content=content)
                else:
                    retries = settings.GENERATION_MAX_RETRIES + 1
                    _update_generation_state(
                        gen_state, path_key, "failed",
                        error=error, retries=retries,
                    )

            # Yield events
            if content:
                yield {
                    "event": "section_done",
                    "data": json.dumps({
                        "path": path_key,
                        "title": title,
                        "content": content,
                        "content_length": len(content),
                        "char_count": len(content),
                        "index": idx + 1,
                        "total": total,
                    }, ensure_ascii=False),
                }
                yield {
                    "event": "subsection_chunk",
                    "data": json.dumps({
                        "chapter_id": path_key,
                        "text": content,
                    }, ensure_ascii=False),
                }
            else:
                yield {
                    "event": "section_error",
                    "data": json.dumps({
                        "path": path_key,
                        "title": title,
                        "error": error,
                        "retry_count": settings.GENERATION_MAX_RETRIES + 1,
                        "index": idx + 1,
                        "total": total,
                    }, ensure_ascii=False),
                }

            # Persist after each section completes (serialised via main coroutine)
            if db:
                try:
                    from sqlalchemy import select as sa_select
                    result_db = await db.execute(
                        sa_select(BidProject).where(BidProject.id == project_id)
                    )
                    db_project = result_db.scalar_one_or_none()
                    if db_project:
                        db_project.generation_state_json = json.dumps(gen_state, ensure_ascii=False)
                        await db.commit()
                except Exception as exc:
                    logger.error("Failed to persist generation state: %s", exc)

            # Yield progress
            yield {
                "event": "progress",
                "data": json.dumps({
                    "completed": gen_state["completed_leaves"],
                    "total": gen_state["total_leaves"],
                    "percentage": round(gen_state["completed_leaves"] / max(gen_state["total_leaves"], 1) * 100, 1),
                }, ensure_ascii=False),
            }

    # ── Phase 4: Assemble into chapters ──
    yield {
        "event": "status",
        "data": json.dumps({
            "phase": "assembling",
            "message": "正在组装章节内容...",
        }, ensure_ascii=False),
    }

    # Build generated_sections dict from gen_state
    generated_sections: Dict[str, str] = {}
    for path_key, sec in gen_state["sections"].items():
        if sec.get("status") == "done" and sec.get("content"):
            generated_sections[path_key] = sec["content"]

    chapters_payload = build_final_chapters_payload(tree, generated_sections)

    # ── Prepend file section chapters (each as a separate chapter) ──
    if file_section_chapters:
        # Insert file sections at the beginning, before AI-generated tech content
        for i, fc in enumerate(reversed(file_section_chapters)):
            fc["order_index"] = i + 1
            chapters_payload.insert(0, fc)
        logger.info("Prepended %d file section chapters", len(file_section_chapters))

    # ── Format verification ──
    if fmt_template and chapters_payload:
        try:
            from app.services.format_verifier import verify_format
            verification = verify_format(chapters_payload, fmt_template)

            yield {
                "event": "format_verification",
                "data": json.dumps(verification, ensure_ascii=False),
            }

            # Save verification result to DB
            if db and project_id:
                try:
                    result_v = await db.execute(
                        sa_select(BidProject).where(BidProject.id == project_id)
                    )
                    db_proj = result_v.scalar_one_or_none()
                    if db_proj:
                        db_proj.format_verification_json = json.dumps(
                            verification, ensure_ascii=False,
                        )
                        await db.commit()
                except Exception as exc:
                    logger.warning("Failed to save verification result: %s", exc)
        except Exception as exc:
            logger.warning("Format verification failed (non-blocking): %s", exc)

    # ── Phase 5: Yield final result ──
    yield {
        "event": "done",
        "data": json.dumps({
            "chapters_count": len(chapters_payload),
            "total_chars": sum(len(c.get("content", "")) for c in chapters_payload),
            "completed_sections": gen_state["completed_leaves"],
            "total_sections": gen_state["total_leaves"],
            "failed_sections": gen_state["total_leaves"] - gen_state["completed_leaves"],
            "chapters": chapters_payload,
        }, ensure_ascii=False),
    }

