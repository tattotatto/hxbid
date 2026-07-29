import pytest
from app.services.format_verifier import (
    verify_format,
    _check_section_completeness,
    _check_section_order,
    _check_numbering_format,
    _check_table_columns,
    _check_signature_blocks,
    _apply_auto_fixes,
)

FORMAT_TEMPLATE = {
    "document_structure": [
        {
            "number": "一",
            "title": "商务部分",
            "required": True,
            "children": [
                {
                    "number": "（一）",
                    "title": "开标一览表",
                    "type": "table",
                    "table_schema": {
                        "columns": [
                            {"name": "序号"},
                            {"name": "服务内容"},
                            {"name": "不含税单价（元）"},
                        ]
                    },
                    "signature_block": {
                        "lines": ["投标人：（公章）", "日期：  年  月  日"]
                    },
                }
            ],
        },
    ],
    "global_format_rules": {"numbering_style": "chinese_legal"},
}


def test_section_completeness_pass():
    chapters = [{"title": "一、商务部分", "content": "..."}]
    results = _check_section_completeness(
        chapters, FORMAT_TEMPLATE["document_structure"]
    )
    assert all(r["status"] == "pass" for r in results)


def test_section_completeness_fail():
    chapters = [{"title": "二、技术部分", "content": "..."}]
    results = _check_section_completeness(
        chapters, FORMAT_TEMPLATE["document_structure"]
    )
    assert any(r["status"] == "fail" for r in results)


def test_table_columns_match():
    content = "| 序号 | 服务内容 | 不含税单价（元） |\n|---|---|---|\n| 1 | 保安 | 100 |"
    schema = FORMAT_TEMPLATE["document_structure"][0]["children"][0]["table_schema"]
    results = _check_table_columns(content, schema, "开标一览表")
    assert all(r["status"] == "pass" for r in results)


def test_table_columns_mismatch():
    content = "| 编号 | 内容 | 金额 |\n|---|---|---|\n| 1 | 保安 | 100 |"
    schema = FORMAT_TEMPLATE["document_structure"][0]["children"][0]["table_schema"]
    results = _check_table_columns(content, schema, "开标一览表")
    assert any(r["status"] == "warning" for r in results)


def test_signature_block_missing():
    content = "普通文本内容，没有签章块"
    sig = FORMAT_TEMPLATE["document_structure"][0]["children"][0]["signature_block"]
    results = _check_signature_blocks(content, sig, "开标一览表")
    assert any(r["status"] == "warning" for r in results)

def test_signature_block_present():
    content = "内容...\n\n投标人：（公章）\n日期：2026年7月29日"
    sig = FORMAT_TEMPLATE["document_structure"][0]["children"][0]["signature_block"]
    results = _check_signature_blocks(content, sig, "开标一览表")
    assert all(r["status"] == "pass" for r in results)

def test_verify_format_empty_template():
    chapters = [{"title": "任意", "content": "..."}]
    result = verify_format(chapters, {})
    assert result["overall_status"] == "pass"
    assert result["message"] == "无格式模板，跳过校验"

def test_verify_format_full():
    chapters = [
        {
            "title": "一、商务部分",
            "content": "| 序号 | 服务内容 | 不含税单价（元） |\n|---|---|---|\n| 1 | 保安 | 100 |\n\n投标人：（公章）\n日期：2026年7月29日",
        }
    ]
    result = verify_format(chapters, FORMAT_TEMPLATE)
    assert result["overall_status"] in ("pass", "pass_with_warnings")
    assert len(result["checks"]) > 0


def test_check_section_order_pass():
    chapters = [{"title": "一、商务部分", "content": "..."}]
    results = _check_section_order(chapters, FORMAT_TEMPLATE["document_structure"])
    assert len(results) == 1
    assert results[0]["status"] == "pass"


def test_check_section_order_mismatch():
    # "技术部分" 不与模板第一个章节 "商务部分" 匹配，且包含关键词 "技术"，
    # 因此会触发 elif 分支，产生 warning
    chapters = [{"title": "技术部分", "content": "..."}]
    results = _check_section_order(chapters, FORMAT_TEMPLATE["document_structure"])
    assert any(r["status"] == "warning" for r in results)


def test_check_numbering_format_chinese_pass():
    content = "# 一、概述\n内容文字"
    results = _check_numbering_format(content, "chinese_legal")
    # No Arabic digits detected in headings — should have no warnings
    assert not any(r["status"] == "warning" for r in results)


def test_check_numbering_format_arabic_warning():
    content = "# 1. 概述\n内容文字"
    results = _check_numbering_format(content, "chinese_legal")
    assert any(r["status"] == "warning" for r in results)
    assert any(r["auto_fix"] == "replace_arabic_with_chinese" for r in results)


def test_apply_auto_fixes_arabic_to_chinese():
    chapters = [
        {
            "title": "一、商务部分",
            "content": "## 1. 开标一览表\n内容文字\n\n## 2. 报价单\n价格信息",
        }
    ]
    verification_results = [
        {
            "check": "numbering_format",
            "item": "heading_numbering",
            "status": "warning",
            "detail": "检测到阿拉伯数字序号",
            "can_auto_fix": True,
            "auto_fix": "replace_arabic_with_chinese",
        }
    ]
    fixed, count = _apply_auto_fixes(chapters, verification_results)
    assert count == 1
    # 原内容 "## 1. 开标" → "## 一、 开标"（保留原空格）
    content = fixed[0]["content"]
    assert "一、" in content and "开标一览表" in content
    assert "二、" in content and "报价单" in content
    assert "## 1." not in content


def test_apply_auto_fixes_no_trigger():
    chapters = [{"title": "商务部分", "content": "普通内容"}]
    verification_results = [
        {
            "check": "section_order",
            "item": "商务部分",
            "status": "warning",
            "detail": "顺序不符",
            "can_auto_fix": False,
        }
    ]
    fixed, count = _apply_auto_fixes(chapters, verification_results)
    # can_auto_fix is False, so no fix should be applied
    assert count == 0
    assert fixed[0]["content"] == "普通内容"
