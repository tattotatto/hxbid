"""Tests for materials injection into bid chapters (bid.py export flow).

Background: user reported that 营业执照/法人身份证/历史合同等材料未插入文档。
根因：原 QUAL_CHAPTER_KEYWORDS 不覆盖用户实际章节标题「投标人基本资料」等。

These tests guard the contract:
1. 公司信息/资质证书 → 命中 QUAL 关键词的章节
2. 项目人员证书 → 命中 PERSONNEL 关键词的章节
3. 历史合同 → 命中 CONTRACT 关键词的章节
4. 兜底：以上都没命中时挂到最后一个章节（材料一定要出现）
"""
from app.services.materials_injection import (
    QUAL_CHAPTER_KEYWORDS,
    PERSONNEL_KEYWORDS,
    CONTRACT_CHAPTER_KEYWORDS,
    inject_materials_into_chapters,
)


def _payload(titles: list[str]) -> tuple[list[dict], list[list[dict]]]:
    chapters = [{"title": t, "content": ""} for t in titles]
    images: list[list[dict]] = [[] for _ in chapters]
    return chapters, images


class TestKeywordsCoverage:
    """用户实际 PDF 第八节「投标人基本资料」必须命中 QUAL（覆盖原关键词缺口）."""

    def test_qual_matches_basic_info_titles(self):
        for title in [
            "投标人基本资料",
            "投标人信息",
            "公司基本情况",
            "公司基本资料",
            "资格审查",
            "公司资质",
            "资质与业绩",
        ]:
            assert any(kw in title for kw in QUAL_CHAPTER_KEYWORDS), (
                f"QUAL keyword missing for title: {title}"
            )

    def test_personnel_matches_personnel_titles(self):
        for title in [
            "项目人员汇总表",
            "人员配置",
            "组织架构",
            "团队介绍",
            "管理架构",
        ]:
            assert any(kw in title for kw in PERSONNEL_KEYWORDS), (
                f"PERSONNEL keyword missing for title: {title}"
            )

    def test_contract_matches_contract_titles(self):
        for title in [
            "类似项目情况表",
            "公司业绩",
            "项目业绩",
            "成功案例",
            "其他材料",
        ]:
            assert any(kw in title for kw in CONTRACT_CHAPTER_KEYWORDS), (
                f"CONTRACT keyword missing for title: {title}"
            )


class TestInjectMaterialsIntoChapters:
    """核心注入逻辑."""

    def test_qual_materials_prepended_to_matching_chapter(self):
        chapters, images = _payload(["投标人基本资料", "服务方案"])
        company_block = "## 公司基本情况\n名称：XX"
        qual_block = "## 资质证书\n1. XXX"
        qual_imgs = [{"path": "/uploads/bl.jpg", "label": "营业执照"}]

        inject_materials_into_chapters(
            chapters=chapters,
            chapter_images=images,
            company_text_block=company_block,
            qual_text_block=qual_block,
            all_qual_section_images=qual_imgs,
            personnel_cert_images=[],
            contract_text_block="",
            contract_images=[],
        )

        # 投标人基本资料 命中 QUAL → 文本 prepend + 图片注入
        assert chapters[0]["content"].startswith(company_block)
        assert qual_block in chapters[0]["content"]
        assert images[0] == qual_imgs
        # 服务方案 未匹配 → 原文不变
        assert chapters[1]["content"] == ""

    def test_contract_materials_appended_to_matching_chapter(self):
        chapters, images = _payload(["类似项目情况表", "其他材料"])
        contract_block = "## 公司业绩一览表\n| 项目 | 金额 |"
        contract_imgs = [{"path": "/uploads/c1.jpg", "label": "合同1"}]

        inject_materials_into_chapters(
            chapters=chapters,
            chapter_images=images,
            company_text_block="",
            qual_text_block="",
            all_qual_section_images=[],
            personnel_cert_images=[],
            contract_text_block=contract_block,
            contract_images=contract_imgs,
        )

        # 类似项目情况表 命中 CONTRACT → 文本 append + 图片注入
        assert contract_block in chapters[0]["content"]
        assert images[0] == contract_imgs

    def test_personnel_images_go_to_personnel_chapter(self):
        chapters, images = _payload(["项目人员汇总表", "服务方案"])
        personnel_imgs = [{"path": "/uploads/p1.jpg", "label": "项目经理证"}]

        inject_materials_into_chapters(
            chapters=chapters,
            chapter_images=images,
            company_text_block="",
            qual_text_block="",
            all_qual_section_images=[],
            personnel_cert_images=personnel_imgs,
            contract_text_block="",
            contract_images=[],
        )

        assert images[0] == personnel_imgs
        assert images[1] == []

    def test_qual_fallback_when_no_match(self):
        """无 QUAL 匹配章节 → 挂到最后一章（材料不丢失）."""
        chapters, images = _payload(["服务方案", "技术方案", "项目实施方案"])
        company_block = "## 公司基本情况\n名称：XX"
        qual_block = "## 资质证书"
        qual_imgs = [{"path": "/uploads/bl.jpg", "label": "营业执照"}]

        inject_materials_into_chapters(
            chapters=chapters,
            chapter_images=images,
            company_text_block=company_block,
            qual_text_block=qual_block,
            all_qual_section_images=qual_imgs,
            personnel_cert_images=[],
            contract_text_block="",
            contract_images=[],
        )

        # 全部内容挂到最后一章（项目实施方案）
        assert company_block in chapters[-1]["content"]
        assert qual_block in chapters[-1]["content"]
        assert images[-1] == qual_imgs
        # 前面章节没被污染
        for ch in chapters[:-1]:
            assert ch["content"] == ""

    def test_contract_fallback_when_no_match(self):
        chapters, images = _payload(["服务方案", "技术方案"])
        contract_block = "## 公司业绩一览表"
        contract_imgs = [{"path": "/uploads/c1.jpg", "label": "合同1"}]

        inject_materials_into_chapters(
            chapters=chapters,
            chapter_images=images,
            company_text_block="",
            qual_text_block="",
            all_qual_section_images=[],
            personnel_cert_images=[],
            contract_text_block=contract_block,
            contract_images=contract_imgs,
        )

        assert contract_block in chapters[-1]["content"]
        assert images[-1] == contract_imgs

    def test_no_materials_no_op(self):
        """没材料 → 不污染任何章节."""
        chapters, images = _payload(["服务方案", "技术方案"])

        inject_materials_into_chapters(
            chapters=chapters,
            chapter_images=images,
            company_text_block="",
            qual_text_block="",
            all_qual_section_images=[],
            personnel_cert_images=[],
            contract_text_block="",
            contract_images=[],
        )

        for ch in chapters:
            assert ch["content"] == ""
        for imgs in images:
            assert imgs == []

    def test_empty_chapters_is_safe(self):
        """空章节列表不应崩."""
        chapters: list[dict] = []
        images: list[list[dict]] = []

        inject_materials_into_chapters(
            chapters=chapters,
            chapter_images=images,
            company_text_block="X",
            qual_text_block="Y",
            all_qual_section_images=[{"path": "/a", "label": "x"}],
            personnel_cert_images=[],
            contract_text_block="Z",
            contract_images=[],
        )

        # 不抛异常，无修改
        assert chapters == []
        assert images == []

    def test_prefers_explicit_match_over_fallback(self):
        """有匹配章节时不走 fallback."""
        chapters, images = _payload(
            ["投标人基本资料", "服务方案", "技术方案"]
        )
        company_block = "## 公司基本情况"

        inject_materials_into_chapters(
            chapters=chapters,
            chapter_images=images,
            company_text_block=company_block,
            qual_text_block="",
            all_qual_section_images=[],
            personnel_cert_images=[],
            contract_text_block="",
            contract_images=[],
        )

        # 注入到第一个匹配（投标人基本资料），不是最后一章
        assert chapters[0]["content"].startswith(company_block)
        assert chapters[-1]["content"] == ""

    def test_qual_and_contract_independent_fallback(self):
        """QUAL 命中但 CONTRACT 没命中 → 只有 CONTRACT 走 fallback."""
        chapters, images = _payload(["投标人基本资料", "服务方案"])
        contract_block = "## 公司业绩一览表"
        contract_imgs = [{"path": "/c1.jpg", "label": "合同1"}]

        inject_materials_into_chapters(
            chapters=chapters,
            chapter_images=images,
            company_text_block="## 公司基本情况",
            qual_text_block="",
            all_qual_section_images=[],
            personnel_cert_images=[],
            contract_text_block=contract_block,
            contract_images=contract_imgs,
        )

        # QUAL 注入到第一章节（投标人基本资料）
        assert "## 公司基本情况" in chapters[0]["content"]
        # CONTRACT fallback 到最后一章节（服务方案）
        assert contract_block in chapters[-1]["content"]
        assert images[-1] == contract_imgs

# ── 法定代表人身份证扫描件：按招标原文的占位行就地插图 ────────────────────
# 用户反馈：「法定代表人授权委托书页面下面需要身份证正面扫描件 身份证反面
# 扫描件，没有插入」。招标原文（玉溪大红山 PDF 第 1732-1733 行）自带
# 「身份证正面扫描件」「身份证反面扫描件」两行占位——那两行就是插图位置。
# 授权委托书 既不命中 QUAL/PERSONNEL/CONTRACT 任何关键词，fixed_form 分支
# 又没有图片标记入口，两处缺口叠加 → 图片从未出现。

ID_SCANS = {
    "front_path": "uploads/company/front.png",
    "front_label": "法定代表人身份证（正面）",
    "back_path": "uploads/company/back.png",
    "back_label": "法定代表人身份证（反面）",
}


def _inject_with_id_scans(chapters, images, id_scans):
    inject_materials_into_chapters(
        chapters=chapters,
        chapter_images=images,
        company_text_block="",
        qual_text_block="",
        all_qual_section_images=[],
        personnel_cert_images=[],
        contract_text_block="",
        contract_images=[],
        legal_rep_id_card_scans=id_scans,
    )


class TestLegalRepIdCardScans:
    """招标原文的「身份证正/反面扫描件」占位行必须就地变成真的图片标记."""

    def test_pair_marker_inserted_after_the_placeholder_lines(self):
        chapters, images = _payload(["法定代表人授权委托书"])
        chapters[0]["content"] = (
            "本人 陈涛系 XX公司的法定代表人。\n"
            "年 月 日\n"
            "身份证正面扫描件\n"
            "身份证反面扫描件\n"
        )

        _inject_with_id_scans(chapters, images, ID_SCANS)

        content = chapters[0]["content"]
        assert "[IDPAIR:" in content
        # 两张图成对，且落在占位行之后（图片要出现在那两行下面）
        assert content.index("[IDPAIR:") > content.index("身份证反面扫描件")

    def test_pair_marker_pairs_front_with_back(self):
        chapters, images = _payload(["法定代表人授权委托书"])
        chapters[0]["content"] = "身份证正面扫描件\n身份证反面扫描件"

        _inject_with_id_scans(chapters, images, ID_SCANS)

        assert (
            "[IDPAIR:uploads/company/front.png|法定代表人身份证（正面）"
            "|uploads/company/back.png|法定代表人身份证（反面）]"
            in chapters[0]["content"]
        )

    def test_身份证号码_lines_do_not_trigger(self):
        """「身份证号码：」不是扫描件占位——不得在那里插图."""
        chapters, images = _payload(["法定代表人授权委托书"])
        original = "法定代表人：\n身份证号码：\n授权委托人：\n身份证号码：\n"
        chapters[0]["content"] = original

        _inject_with_id_scans(chapters, images, ID_SCANS)

        assert chapters[0]["content"] == original

    def test_prose_mentioning_id_scans_does_not_trigger(self):
        """夹在句子里的「身份证…扫描件」不是占位行，不得触发."""
        chapters, images = _payload(["项目人员配置"])
        original = "附：身份证、职称证（如有）、执业证书（如有）等扫描件。\n"
        chapters[0]["content"] = original

        _inject_with_id_scans(chapters, images, ID_SCANS)

        assert chapters[0]["content"] == original

    def test_paste_placeholder_line_triggers(self):
        """另一份标书的写法：「[身份证复印件粘贴处]」."""
        chapters, images = _payload(["法定代表人授权委托书"])
        chapters[0]["content"] = "附：授权代理人身份证复印件\n\n[身份证复印件粘贴处]\n"

        _inject_with_id_scans(chapters, images, ID_SCANS)

        assert "[IDPAIR:" in chapters[0]["content"]

    def test_single_side_scan_uses_img_marker(self):
        """只有正面时出单张图，不拼半拉子的 IDPAIR."""
        chapters, images = _payload(["法定代表人授权委托书"])
        chapters[0]["content"] = "身份证正面扫描件\n"

        _inject_with_id_scans(
            chapters, images, {"front_path": "uploads/company/front.png",
                               "front_label": "法定代表人身份证（正面）"}
        )

        content = chapters[0]["content"]
        assert "[IDPAIR:" not in content
        assert "[IMG:uploads/company/front.png|法定代表人身份证（正面）]" in content

    def test_placeholder_without_scans_leaves_content_untouched(self):
        """资料库里没传身份证 → 原文原样留着，不写空标记."""
        chapters, images = _payload(["法定代表人授权委托书"])
        original = "身份证正面扫描件\n身份证反面扫描件\n"
        chapters[0]["content"] = original

        _inject_with_id_scans(chapters, images, {})

        assert chapters[0]["content"] == original

    def test_scans_do_not_leak_into_unrelated_chapters(self):
        """占位行只出现在授权委托书里——别的章节不得被塞图."""
        chapters, images = _payload(["法定代表人授权委托书", "服务方案"])
        chapters[0]["content"] = "身份证正面扫描件\n身份证反面扫描件\n"
        chapters[1]["content"] = "服务方案正文，无占位。"

        _inject_with_id_scans(chapters, images, ID_SCANS)

        assert "[IDPAIR:" not in chapters[1]["content"]
