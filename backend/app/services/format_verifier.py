"""宏曦标书 - 格式校验器.
"""
import logging
import re
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def _check_section_completeness(
    chapters_payload: list[dict],
    structure: list[dict],
) -> list[dict]:
    """检查章节完整性：required=true 的章节是否全部存在."""
    results = []
    # 收集已生成的章节标题
    generated_titles = [ch.get("title", "") for ch in chapters_payload]

    for part in structure:
        if not part.get("required", True):
            continue
        part_title = part.get("title", "")
        # 检查顶层部分是否存在
        found = any(part_title in t for t in generated_titles)
        if not found:
            results.append({
                "check": "section_completeness",
                "item": part_title,
                "status": "fail",
                "detail": f"缺少必需章节：{part_title}",
                "can_auto_fix": False,
            })
        else:
            results.append({
                "check": "section_completeness",
                "item": part_title,
                "status": "pass",
                "detail": "",
                "can_auto_fix": False,
            })

    return results


def _check_section_order(
    chapters_payload: list[dict],
    structure: list[dict],
) -> list[dict]:
    """检查章节排列顺序是否与模板一致."""
    # 获取模板规定的顺序
    expected_order = [
        f"{p.get('number', '')}、{p.get('title', '')}"
        for p in structure
    ]
    # 获取生成内容的顺序
    actual_order = [ch.get("title", "") for ch in chapters_payload]

    # 检查相对顺序
    mismatches = []
    exp_idx = 0
    for act_idx, act_title in enumerate(actual_order):
        if exp_idx >= len(expected_order):
            break
        exp_title = expected_order[exp_idx]
        # 模糊匹配：检查 actual title 是否包含 expected title 的关键部分
        exp_key = re.sub(r'[一二三四五六七八九十\d]+、', '', exp_title).strip()
        if exp_key in act_title:
            exp_idx += 1
        elif any(kw in act_title for kw in ["商务", "技术", "资格审查", "其他"]):
            # 出现了一个模板中不存在的顶层章节
            mismatches.append({
                "check": "section_order",
                "item": act_title,
                "status": "warning",
                "detail": f"章节'{act_title}'的顺序与模板不符",
                "can_auto_fix": False,
            })
        else:
            # 章节标题不匹配任何已知关键词，可能存在顺序问题
            mismatches.append({
                "check": "section_order",
                "item": act_title,
                "status": "warning",
                "detail": f"章节'{act_title}'未匹配任何预期模板元素或已知关键词，请人工复核顺序",
                "can_auto_fix": False,
            })

    if not mismatches:
        return [{
            "check": "section_order",
            "item": "all",
            "status": "pass",
            "detail": f"章节顺序与模板一致",
            "can_auto_fix": False,
        }]
    return mismatches


def _check_numbering_format(
    content: str,
    expected_style: str = "chinese_legal",
) -> list[dict]:
    """检查序号格式是否符合模板要求（如：一、（一）、1.、（1））."""
    results = []
    # 检查是否使用了正确的序号体系
    if expected_style == "chinese_legal":
        # 检查一级序号是否是中文数字
        if re.search(r'^#{1,2}\s*\d+[、.]', content, re.MULTILINE):
            results.append({
                "check": "numbering_format",
                "item": "heading_numbering",
                "status": "warning",
                "detail": "检测到阿拉伯数字序号，应为中文数字（一、二、三...）",
                "can_auto_fix": True,
                "auto_fix": "replace_arabic_with_chinese",
            })
    elif expected_style == "numeric":
        # 检查一级序号是否是阿拉伯数字（不应使用中文数字）
        if re.search(r'^#{1,2}\s*[一二三四五六七八九十]+[、]', content, re.MULTILINE):
            results.append({
                "check": "numbering_format",
                "item": "heading_numbering",
                "status": "warning",
                "detail": "检测到中文数字序号，应为阿拉伯数字（1、2、3...）",
                "can_auto_fix": True,
                "auto_fix": "replace_chinese_with_arabic",
            })
    return results


def _check_table_columns(
    content: str,
    table_schema: dict | None,
    section_title: str = "",
) -> list[dict]:
    """检查表格列定义是否与模板一致."""
    if not table_schema:
        return []

    results = []
    expected_columns = [c["name"] for c in table_schema.get("columns", [])]

    # 在内容中查找第一个 markdown 表格
    table_match = re.search(
        r'^\|(.+)\|\s*$\n^\|[\s\-:]+\|',
        content, re.MULTILINE,
    )
    if not table_match:
        # 未找到表格 — 可能使用了分号分隔格式
        sc_match = re.search(r'([^；]+：.+)', content)
        if not sc_match:
            results.append({
                "check": "table_columns",
                "item": section_title,
                "status": "warning",
                "detail": f"未检测到表格，预期列：{'、'.join(expected_columns)}",
                "can_auto_fix": False,
            })
        return results

    # 解析实际列名
    header_row = table_match.group(1)
    actual_columns = [c.strip() for c in header_row.split('|') if c.strip()]

    # 比较列数
    if len(actual_columns) != len(expected_columns):
        results.append({
            "check": "table_columns",
            "item": section_title,
            "status": "warning",
            "detail": f"表格列数不匹配：实际{len(actual_columns)}列，预期{len(expected_columns)}列（{'、'.join(expected_columns)}）",
            "can_auto_fix": False,
        })
        return results

    # 比较列名（模糊匹配）
    col_mismatches = []
    for i, (act, exp) in enumerate(zip(actual_columns, expected_columns)):
        # 提取核心关键词比较
        act_clean = re.sub(r'[（(].*?[)）]', '', act).strip()
        exp_clean = re.sub(r'[（(].*?[)）]', '', exp).strip()
        if act_clean != exp_clean and act_clean not in exp_clean and exp_clean not in act_clean:
            col_mismatches.append(f"第{i+1}列：'{act}'≠'{exp}'")

    if col_mismatches:
        results.append({
            "check": "table_columns",
            "item": section_title,
            "status": "warning",
            "detail": f"列名偏差：{'；'.join(col_mismatches)}",
            "can_auto_fix": True,
            "auto_fix": "replace_column_names",
            "expected_columns": expected_columns,
        })
    else:
        results.append({
            "check": "table_columns",
            "item": section_title,
            "status": "pass",
            "detail": f"表格列定义与模板一致（{len(expected_columns)}列）",
            "can_auto_fix": False,
        })

    return results


def _check_signature_blocks(
    content: str,
    expected_signature: dict | None,
    section_title: str = "",
) -> list[dict]:
    """检查签章块是否存在."""
    if not expected_signature:
        return []

    required_lines = expected_signature.get("lines", [])
    if not required_lines:
        return []

    missing_lines = []
    for line in required_lines:
        # 模糊匹配：检查关键词是否存在
        key_parts = re.split(r'[：:（）()]', line)
        key_words = [p.strip() for p in key_parts if len(p.strip()) >= 2]
        key_found = any(kw in content for kw in key_words)
        if not key_found:
            missing_lines.append(line)

    if missing_lines:
        return [{
            "check": "signature_block",
            "item": section_title,
            "status": "warning",
            "detail": f"缺少签章行：{'；'.join(missing_lines)}",
            "can_auto_fix": True,
            "auto_fix": "append_signature",
            "missing_lines": required_lines,
        }]
    return [{
        "check": "signature_block",
        "item": section_title,
        "status": "pass",
        "detail": "签章块完整",
        "can_auto_fix": False,
    }]


def _apply_auto_fixes(
    chapters_payload: list[dict],
    verification_results: list[dict],
) -> tuple[list[dict], int]:
    """应用自动修正."""
    fixes_applied = 0
    for result in verification_results:
        if not result.get("can_auto_fix"):
            continue

        fix_type = result.get("auto_fix", "")

        if fix_type == "replace_arabic_with_chinese":
            # 将内容中 markdown 标题的阿拉伯数字序号替换为中文数字
            arabic_to_chinese = {
                "1": "一", "2": "二", "3": "三", "4": "四", "5": "五",
                "6": "六", "7": "七", "8": "八", "9": "九", "10": "十",
                "11": "十一", "12": "十二", "13": "十三", "14": "十四",
                "15": "十五", "16": "十六", "17": "十七", "18": "十八",
                "19": "十九", "20": "二十",
            }
            item = result.get("item", "")
            for ch in chapters_payload:
                if item not in ch.get("title", "") and item != "heading_numbering":
                    continue
                content = ch.get("content", "")
                if not content:
                    continue

                def _replace_heading_num(match):
                    prefix = match.group(1)
                    num = match.group(2)
                    punct = match.group(3)  # 、or . or ．
                    cn = arabic_to_chinese.get(num)
                    if cn is None:
                        return match.group(0)
                    # Use Chinese-style punctuation
                    sep = "、" if punct in ("、", ".", "．") else punct
                    return f"{prefix}{cn}{sep}"

                new_content = re.sub(
                    r'^(#{1,2}\s*)(\d+)([、.．])',
                    _replace_heading_num,
                    content,
                    flags=re.MULTILINE,
                )
                if new_content != content:
                    ch["content"] = new_content
                    fixes_applied += 1
                    result["auto_fix_applied"] = True

        elif fix_type == "replace_column_names":
            expected = result.get("expected_columns", [])
            item = result.get("item", "")
            for ch in chapters_payload:
                if item in ch.get("title", ""):
                    content = ch.get("content", "")
                    # 替换表格头行中的列名
                    header_match = re.search(
                        r'^\|(.+)\|\s*$',
                        content, re.MULTILINE,
                    )
                    if header_match:
                        new_header = '| ' + ' | '.join(expected) + ' |'
                        old_header = header_match.group(0)
                        ch["content"] = content.replace(old_header, new_header, 1)
                        fixes_applied += 1
                        result["auto_fix_applied"] = True

        elif fix_type == "append_signature":
            missing = result.get("missing_lines", [])
            item = result.get("item", "")
            for ch in chapters_payload:
                if item in ch.get("title", ""):
                    sig_block = "\n\n" + "\n".join(missing) + "\n"
                    ch["content"] = (ch.get("content") or "") + sig_block
                    fixes_applied += 1
                    result["auto_fix_applied"] = True

    return chapters_payload, fixes_applied


def verify_format(
    chapters_payload: list[dict],
    format_template: dict,
) -> dict:
    """校验生成内容是否符合招标文件格式要求.

    Args:
        chapters_payload: 生成的章节内容列表 [{"title": ..., "content": ...}, ...]
        format_template: 从招标文件提取的格式模板

    Returns:
        校验报告 dict:
        {
            "overall_status": "pass" | "pass_with_warnings" | "fail",
            "checks": [...],
            "auto_fixes_applied": int,
            "manual_review_required": int,
        }
    """
    if not format_template or not format_template.get("document_structure"):
        return {
            "overall_status": "pass",
            "checks": [],
            "auto_fixes_applied": 0,
            "manual_review_required": 0,
            "message": "无格式模板，跳过校验",
        }

    structure = format_template.get("document_structure", [])
    global_rules = format_template.get("global_format_rules", {})
    all_checks: list[dict] = []

    # 1. 章节完整性检查
    all_checks.extend(_check_section_completeness(chapters_payload, structure))

    # 2. 章节顺序检查
    all_checks.extend(_check_section_order(chapters_payload, structure))

    # 3. 逐章节内容检查（表格列、签章块）
    for part in structure:
        part_title = part.get("title", "")
        numbering_style = global_rules.get("numbering_style", "chinese_legal")

        # 找到对应的生成章节
        for ch in chapters_payload:
            if part_title in ch.get("title", ""):
                content = ch.get("content", "")

                # 序号格式检查
                all_checks.extend(_check_numbering_format(
                    content, numbering_style,
                ))

                # 遍历子章节
                for child in part.get("children", []):
                    child_title = child.get("title", "")
                    child_type = child.get("type", "")

                    if child_type == "table":
                        all_checks.extend(_check_table_columns(
                            content, child.get("table_schema"), child_title,
                        ))

                    if child.get("signature_block"):
                        all_checks.extend(_check_signature_blocks(
                            content, child.get("signature_block"), child_title,
                        ))


    # 4. 应用自动修正
    chapters_payload, fixes_applied = _apply_auto_fixes(chapters_payload, all_checks)

    # 5. 汇总
    fail_count = sum(1 for c in all_checks if c.get("status") == "fail")
    warning_count = sum(1 for c in all_checks if c.get("status") == "warning")
    manual_review = sum(1 for c in all_checks
                        if c.get("status") in ("fail", "warning")
                        and not c.get("can_auto_fix"))

    if fail_count > 0:
        overall = "fail"
    elif warning_count > 0:
        overall = "pass_with_warnings"
    else:
        overall = "pass"

    return {
        "overall_status": overall,
        "checks": all_checks,
        "auto_fixes_applied": fixes_applied,
        "manual_review_required": manual_review,
        "fail_count": fail_count,
        "warning_count": warning_count,
        "pass_count": len(all_checks) - fail_count - warning_count,
    }
