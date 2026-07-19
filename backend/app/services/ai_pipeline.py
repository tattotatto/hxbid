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
from typing import Any, AsyncIterator, Callable, Dict, List

from app.config import settings
from app.services.ai_adapter import ai_adapter
from app.services.deid import deidentify_text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

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
   - 每个 ## 节下面至少写2-3段正文，再根据需要添加 ### 小节
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
8. 公司基本信息（公司名称、法定代表人、统一社会信用代码、地址、联系电话等）必须使用输入中提供的真实数据原文照搬，严禁编造或改写。禁止凭空编造任何人名（法定代表人、授权代表、项目负责人等），这些信息只能从输入提供的资料中获取。如某项信息在输入资料中未提供，使用"[待补充]"标记，不得自行编造

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
- 如果某项信息在输入资料中标记为[未填写]或未提供，标书中对应位置留空或写"[待补充]"，严禁自行编造填充"""

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
    lines.append("重要提醒：标书中涉及上述信息时，必须使用以上真实数据，不得自行编造任何公司名称、人员姓名、证照编号等信息。如某字段标注为[未填写]，请在标书中留空或写[待补充]，不得编造。")

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


def _build_system_prompt(extra_constraints: List[str] | None = None) -> str:
    """Build the full system prompt, appending any active feedback rules."""
    parts = [SYSTEM_PROMPT]
    all_constraints = list(_active_constraints_cache)
    if extra_constraints:
        all_constraints.extend(extra_constraints)
    if all_constraints:
        parts.append("\n额外写作约束（基于历史编辑反馈自动生成）：")
        for i, c in enumerate(all_constraints, 1):
            parts.append(f"  {i}. {c}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Helper: build messages list with system prompt prepended
# ---------------------------------------------------------------------------

def _build_messages(user_content: str, extra_constraints: List[str] | None = None) -> List[Dict[str, str]]:
    """Return a messages list with SYSTEM_PROMPT as the system message."""
    return [
        {"role": "system", "content": _build_system_prompt(extra_constraints)},
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

async def parse_bid_requirements(document_text: str) -> dict:
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

注意：
- 所有字段都必须存在，未提及的字段使用空字符串或空数组
- 直接返回JSON对象，不要包含任何其他文字说明

招标文件内容：
{truncated}"""

    messages = _build_messages(user_prompt)

    response = await ai_adapter.chat_completion(
        messages=messages,
        temperature=0.3,
        response_format={"type": "json_object"},
    )

    try:
        result = json.loads(response)
    except json.JSONDecodeError:
        # Return a safe default structure on parse failure
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
    }
    for key, default in defaults.items():
        if key not in result:
            result[key] = default

    return result


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

def _get_section_guidance(chapter_title: str) -> str:
    """Return content-structure guidance for a given chapter title.

    Bidding documents (招标文件) typically divide the bid into four parts
    (see 第六章 投标文件格式).  Each part has a clear purpose; without
    explicit guidance the AI tends to put commitment letters everywhere
    and duplicate content across sections.
    """
    title_lower = chapter_title.strip().lower()

    # -- 商务部分 / Business Section --
    if _match_section(title_lower, ["商务", "商务部分"]):
        return """【本章节内容规范 — 商务部分】
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
    if _match_section(title_lower, ["技术", "技术部分", "服务方案", "技术方案"]):
        return """【本章节内容规范 — 技术部分】
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
    if _match_section(title_lower, ["资格审查", "资格", "资质审查", "公司资质", "资质与业绩"]):
        return """【本章节内容规范 — 资格审查部分】
本章节用于证明投标人具备投标资格，必须包含：
1. 投标人基本情况表（公司名称、统一社会信用代码、法定代表人、注册资本、成立时间、经营范围、公司简介）
2. 营业执照扫描件说明（注明"营业执照副本复印件加盖公章附后"）
3. 有效的保安服务许可证/行业资质证书扫描件说明（注明资质名称、编号、发证机关、有效期）
4. 企业信誉情况承诺书（承诺：未被暂停投标资格、未列入严重失信名单、未列入行贿行为供应商名单）
5. 项目人员承诺书（承诺人员数量、资质、劳动合同、社保、无犯罪记录等符合招标要求）

注意：
- 本章节重点展示公司资质证照（营业执照、经营许可证等），这些证照已在系统"公司资质"中上传
- 如系统提供了资质证书资料，应逐一列出证书名称和编号
- 不得包含廉洁诚信承诺书（该承诺书在商务部分）
- 不得包含与招标人干部职工不存在关联关系的承诺书（该承诺书在商务部分）
- 企业信誉情况承诺书与廉洁诚信承诺书是不同的文件，不可混淆"""

    # -- 投标人认为需要提供的其他内容 / Other Materials --
    if _match_section(title_lower, ["其他", "其他内容", "投标人认为需要提供的"]):
        return """【本章节内容规范 — 投标人认为需要提供的其他内容】
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
    if _match_section(title_lower, ["投标函"]):
        return """【本章节内容规范 — 投标函】
按招标文件格式撰写正式投标函：
1. 致招标人全称
2. 声明已仔细阅读全部招标文件内容
3. 承诺投标有效期（从投标截止日起120个日历天）
4. 声明独立投标、无联合体
5. 承诺不挂靠、不串标围标
6. 中标承诺（按期签约、缴纳履约担保、按期履约、不转包分包）
7. 同意投标保证金没收情形
8. 附投标人签章栏"""

    # -- Catch-all: no specific guidance --
    return ""

def _match_section(title_lower: str, keywords: list) -> bool:
    """Return True if any keyword appears in the chapter title."""
    return any(kw in title_lower for kw in keywords)


# ---------------------------------------------------------------------------
# 3. generate_chapter
# ---------------------------------------------------------------------------

async def generate_chapter(
    chapter_title: str,
    requirements: dict,
    context: str = "",
    stream: bool = True,
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
    section_guidance = _get_section_guidance(chapter_title)

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

    messages = _build_messages(user_prompt)

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
    similar_chapters: list[str] | None = None,
    company_profile: dict | None = None,
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
    )

    async for chunk in stream_result:
        yield chunk


# ---------------------------------------------------------------------------
# Generation state helpers
# ---------------------------------------------------------------------------

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


async def _generate_single_section_with_retry(
    leaf: dict,
    requirements: dict,
    company_profile: dict | None,
    reference_sections: list,
    max_retries: int = 2,
    retry_delay_base: float = 1.0,
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
    sibling_summaries = leaf.get("sibling_summaries", [])

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
                sibling_summaries=sibling_summaries[:8],
                reference_sections=reference_sections,
                company_profile=company_profile,
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

async def generate_bid_with_deep_outline(
    requirements: dict,
    company_profile: dict | None = None,
    matched_qualifications: list | None = None,
    matched_personnel: list | None = None,
    project_id: str = "",
    db=None,
    progress_callback: Callable | None = None,
) -> AsyncIterator[dict]:
    """Generate a complete bid document — incremental per-section generation.

    No ThreadPoolExecutor. Each leaf section is generated sequentially in
    the main async context. Each section is persisted immediately.
    """
    from app.services.outline_engine import generate_deep_outline
    from app.services.subsection_generator import prepare_outline_tree, get_outline_stats
    from app.services.content_assembler import build_final_chapters_payload
    from app.services.reference_analyzer import get_reference_outlines
    from app.services.rag import retrieve_similar_chapters
    from app.services.token_budget import collect_leaf_sections
    from app.models.project import BidProject

    # ── Phase 1: Build reference outlines ──
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
        "event": "outline_ready",
        "data": json.dumps({
            "total_leaves": stats["total_leaf_sections"],
            "estimated_pages": stats["estimated_pages"],
            "max_depth": stats["max_depth"],
            "completed_from_previous": gen_state["completed_leaves"],
            "outline_tree": tree,
        }, ensure_ascii=False),
    }

    # ── Phase 3: Generate sections one by one (NO THREAD POOL) ──
    yield {
        "event": "status",
        "data": json.dumps({
            "phase": "generating",
            "message": f"开始逐节生成（共 {stats['total_leaf_sections']} 个小节，已完成 {gen_state['completed_leaves']} 个）...",
            "total_leaf_sections": stats["total_leaf_sections"],
            "completed_leaf_sections": gen_state["completed_leaves"],
        }, ensure_ascii=False),
    }

    # Collect sibling titles per section for anti-duplication
    all_titles = [leaf.get("title", "") for leaf in leaves]

    total = len(leaves)
    for idx, leaf in enumerate(leaves):
        path_key = " > ".join(leaf.get("path", []))
        sec_state = gen_state["sections"].get(path_key, {})

        # Skip already completed
        if sec_state.get("status") == "done" and sec_state.get("content"):
            logger.info("Skipping already-completed section: %s", path_key)
            continue

        # Skip sections that failed max retries (unless we're explicitly retrying)
        if sec_state.get("status") == "failed" and sec_state.get("retries", 0) >= settings.GENERATION_MAX_RETRIES + 1:
            continue

        # ── Emit section_start ──
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

        # ── Update state to generating ──
        _update_generation_state(gen_state, path_key, "generating")
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
            except Exception:
                pass

        # ── Fetch reference sections via RAG ──
        reference_sections: list = []
        if db:
            try:
                similar = await retrieve_similar_chapters(
                    chapter_title=leaf.get("title", ""),
                    requirements=requirements,
                    project_id=project_id,
                )
                reference_sections = [s.get("content", "") for s in similar if s.get("content")]
            except Exception as exc:
                logger.debug("RAG failed for '%s': %s", leaf.get("title", ""), exc)

        # ── Build sibling summaries ──
        sibling_summaries = [f"{t}（详见该章节）" for t in all_titles if t != leaf.get("title", "")]
        leaf["sibling_summaries"] = sibling_summaries

        # ── Generate with retry ──
        content, error = await _generate_single_section_with_retry(
            leaf=leaf,
            requirements=requirements,
            company_profile=company_profile,
            reference_sections=reference_sections,
            max_retries=settings.GENERATION_MAX_RETRIES,
            retry_delay_base=settings.GENERATION_RETRY_DELAY_BASE,
        )

        if content:
            # ── Success ──
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
        else:
            # ── Failed ──
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

        # ── Persist after EVERY section ──
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

        # ── Yield overall progress ──
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

