import pytest
from app.services.format_verifier import (
    verify_format,
    _check_section_completeness,
    _check_table_columns,
    _check_signature_blocks,
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
