"""宏曦标书 - 素材上下文构建 单元测试.
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""
from app.services.materials_context import (
    build_qualifications_context,
    build_contract_context,
    build_personnel_context,
    assemble_section_materials,
)


class TestBuildQualificationsContext:
    def test_formats_qualifications(self):
        quals = [
            {"name": "保安服务许可证", "cert_number": "云保001", "issuing_authority": "云南省公安厅"},
        ]
        text = build_qualifications_context(quals)
        assert "保安服务许可证" in text
        assert "云保001" in text
        assert "严禁编造" in text

    def test_empty_returns_empty(self):
        assert build_qualifications_context([]) == ""


class TestAssembleSectionMaterials:
    def test_qualification_section_gets_qual_ctx(self):
        text = assemble_section_materials(
            "公司资质及资格证书",
            qualifications=[{"name": "保安服务许可证", "cert_number": "001"}],
            personnel=[{"name": "李四", "role": "项目经理"}],
            contracts=[{"project_name": "某项目"}],
        )
        assert "保安服务许可证" in text
        assert "李四" not in text      # 资质章节不注入人员
        assert "某项目" not in text    # 资质章节不注入合同

    def test_personnel_section_gets_personnel_ctx(self):
        text = assemble_section_materials(
            "拟投入项目人员配置表",
            qualifications=[{"name": "保安服务许可证"}],
            personnel=[{"name": "李四", "role": "项目经理"}],
            contracts=[{"project_name": "某项目"}],
        )
        assert "李四" in text
        assert "保安服务许可证" not in text

    def test_company_always_injected(self):
        text = assemble_section_materials(
            "服务方案",
            company={"company_name": "云南领航保安服务有限公司"},
        )
        assert "云南领航保安服务有限公司" in text

    def test_truncated_to_max_chars(self):
        big = [{"name": "证书A", "cert_number": "X" * 5000} for _ in range(10)]
        text = assemble_section_materials("资质证书", qualifications=big)
        assert len(text) <= 6000
