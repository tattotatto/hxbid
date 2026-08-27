"""宏曦标书 - 素材上下文构建.

把公司资质 / 人员 / 历史合同 / 公司信息四类素材按章节标题关键词
组装成 AI 提示上下文。供生成管线、单节重新生成、章节对话共用。

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

MATERIALS_CTX_MAX_CHARS = 6000

# 关键词组：标题命中即注入对应素材块
QUAL_CTX_KEYWORDS = [
    "资质", "证书", "资格", "认证", "许可证", "营业执照",
    "证件", "证明文件", "质量管理", "管理体系",
]
CONTRACT_KEYWORDS = [
    "业绩", "类似项目", "项目经验", "成功案例", "既往", "合同业绩",
    "投标人认为需要提供的其他", "其他内容", "其他材料",
]
PERSONNEL_CTX_KEYWORDS = [
    "人员", "配置", "团队", "组织", "人力", "管理架构", "岗位",
    "项目负责人", "项目经理", "拟投入", "技术负责人",
]


def build_qualifications_context(qualifications: list) -> str:
    """格式化公司资质为提示上下文."""
    items = [q for q in (qualifications or []) if q and q.get("name")]
    if not items:
        return ""
    lines = ["【公司资质证件数据 — 以下为真实资质数据，标书中涉及资质、证书时必须原样使用，严禁编造】"]
    for q in items:
        lines.append(f"  - {q['name']}")
        if q.get("cert_number"):
            lines.append(f"    证书编号：{q['cert_number']}")
        if q.get("issuing_authority"):
            lines.append(f"    发证机构：{q['issuing_authority']}")
    lines.append("重要提醒：标书中涉及资质证书时，必须使用以上真实资质数据，不得编造证书编号或机构。")
    return "\n".join(lines)


def build_personnel_context(personnel: list) -> str:
    """格式化项目人员为提示上下文."""
    lines = ["【可用项目人员 — 以下为真实人员数据，标书中涉及人员配置时必须使用，严禁编造姓名、证书等信息】"]
    count = 0
    for p in (personnel or []):
        if not p or not p.get("name"):
            continue
        role = p.get("role", "")
        edu = p.get("education", "")
        tags = p.get("tags", "")
        certs = p.get("certificates") or []
        lines.append(f"  - {p['name']}（{role}，学历{edu}）")
        if tags:
            lines.append(f"    持证/特长：{tags}")
        for c in certs:
            cn = c.get("cert_name", "") if isinstance(c, dict) else getattr(c, "cert_name", "")
            if cn:
                lines.append(f"    证书：{cn}")
        count += 1
    if count == 0:
        return ""
    lines.append("重要提醒：标书中涉及人员配置时，只能使用以上真实人员数据，严禁编造任何人名或证书信息。")
    return "\n".join(lines)


def build_contract_context(contracts: list) -> str:
    """格式化历史合同为提示上下文."""
    items = [c for c in (contracts or []) if c and c.get("project_name")]
    if not items:
        return ""
    lines = ["【历史合同业绩数据 — 以下为真实项目数据，必须在标书中原样使用，严禁编造项目名称、金额等信息】"]
    for i, c in enumerate(items, 1):
        lines.append(f"{i}. 项目名称：{c['project_name']}")
        if c.get("procurement_unit"):
            lines.append(f"   采购单位：{c['procurement_unit']}")
        if c.get("contract_amount"):
            lines.append(f"   合同金额：{c['contract_amount']}")
        if c.get("contract_date"):
            lines.append(f"   合同日期：{c['contract_date']}")
        lines.append("")
    lines.append("重要提醒：标书中涉及项目业绩时，必须使用以上真实项目数据，不得自行编造。")
    return "\n".join(lines)


def build_company_context(company: dict | None) -> str:
    """格式化公司信息为提示上下文.

    字段来自 get_collected_resources 的 company 块（company_name /
    business_license_number / legal_rep_name / legal_rep_id_number /
    address / contact_phone / website / notes）。字段缺失以 [未填写] 占位，
    备注（notes）字段一并注入；渲染与 ai_pipeline.build_company_info_block 对齐。
    """
    if not company:
        return ""

    fields = [
        ("公司名称", "company_name"),
        ("统一社会信用代码", "business_license_number"),
        ("法定代表人", "legal_rep_name"),
        ("法定代表人身份证号", "legal_rep_id_number"),
        ("公司地址", "address"),
        ("联系电话", "contact_phone"),
        ("公司网站", "website"),
    ]

    lines = ["【公司基本信息 — 以下为真实公司数据，标书中涉及公司信息时必须原样使用，严禁编造】"]
    for label, key in fields:
        value = (company.get(key) or "").strip()
        if value:
            lines.append(f"  {label}：{value}")
        else:
            lines.append(f"  {label}：[未填写]")

    # 备注字段一并注入，避免公司备注在新管线丢失
    notes = (company.get("notes") or "").strip()
    if notes:
        lines.append(f"  备注：{notes}")

    lines.append("")
    lines.append(
        "重要提醒：标书中公司名称、统一社会信用代码等必须以真实数据为准，不得编造。"
        "如某字段标注为[未填写]，请在标书中留空或写[待补充]，不得编造。"
    )
    return "\n".join(lines)


def assemble_section_materials(
    section_title: str,
    *,
    qualifications: list | None = None,
    personnel: list | None = None,
    contracts: list | None = None,
    company: dict | None = None,
) -> str:
    """按章节标题关键词组装该节所需的素材上下文.

    公司信息始终注入；资质/人员/合同按标题关键词命中注入。
    整体截断到 MATERIALS_CTX_MAX_CHARS，防素材反噬上下文。
    """
    parts: list[str] = []
    title_lower = (section_title or "").lower()

    if any(kw in title_lower for kw in QUAL_CTX_KEYWORDS):
        parts.append(build_qualifications_context(qualifications or []))
    if any(kw in title_lower for kw in PERSONNEL_CTX_KEYWORDS):
        parts.append(build_personnel_context(personnel or []))
    if any(kw in title_lower for kw in CONTRACT_KEYWORDS):
        parts.append(build_contract_context(contracts or []))

    company_ctx = build_company_context(company)
    if company_ctx:
        parts.append(company_ctx)

    joined = "\n\n".join(p for p in parts if p)
    if len(joined) > MATERIALS_CTX_MAX_CHARS:
        joined = joined[:MATERIALS_CTX_MAX_CHARS]
    return joined
