"""Tests for outline_engine format_template integration.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from app.services.outline_engine import _format_template_to_prompt_text

FORMAT_TEMPLATE = {
    "document_structure": [
        {
            "number": "一",
            "title": "商务部分",
            "required": True,
            "children": [
                {"number": "（一）", "title": "投标函", "type": "fixed_form"},
                {
                    "number": "（二）",
                    "title": "开标一览表",
                    "type": "table",
                    "table_schema": {"columns": [{"name": "序号"}, {"name": "金额"}]},
                },
            ],
        },
        {
            "number": "二",
            "title": "技术部分",
            "required": True,
            "children": [
                {"number": "（一）", "title": "服务方案", "type": "text"},
                {"number": "（二）", "title": "资质证书", "type": "attachment"},
            ],
        },
        {
            "number": "三",
            "title": "其他材料",
            "required": False,
            "children": [],
        },
    ],
}


def test_format_template_to_prompt_text():
    text = _format_template_to_prompt_text(FORMAT_TEMPLATE)
    assert "一、商务部分" in text
    # 顶层标记只有三种：技术类 / 可选（文件类标题走另一分支）。旧文案
    # 「【必需】」已废弃——「需要你设计深层大纲」比「必需」更能说明让 AI
    # 做什么，光说必需它不知道该展开到多深。
    assert "【技术类 — 需要你设计深层大纲】" in text
    assert "【可选】" in text
    # 子项按 type 标注系统会怎么处理它
    assert "（一） 投标函 [固定格式 — 系统自动生成]" in text
    assert "（二） 开标一览表 [表格 — 系统自动生成]" in text
    assert "（一） 服务方案 [需要扩展深层大纲]" in text
    assert "（二） 资质证书 [附件 — 系统自动处理]" in text
    assert "三、其他材料 【可选】" in text
    # Check closing line
    assert "共 3 个部分" in text


def test_empty_template():
    text = _format_template_to_prompt_text({})
    assert len(text) > 0
    assert "共 0 个部分" in text


def test_template_without_document_structure():
    text = _format_template_to_prompt_text({"other_key": "value"})
    assert len(text) > 0
    assert "共 0 个部分" in text


def test_template_child_without_type():
    template = {
        "document_structure": [
            {
                "number": "一",
                "title": "测试部分",
                "required": True,
                "children": [
                    {"number": "1", "title": "子项无类型"},
                ],
            },
        ],
    }
    text = _format_template_to_prompt_text(template)
    assert "一、测试部分" in text
    assert "1 子项无类型" in text
    # No type hint should be appended for plain text
    assert "表格" not in text
    assert "固定格式表单" not in text
    assert "附件/证明材料" not in text
