"""Tests for materials injection into bid chapters (bid.py export flow).

Background: user reported that 营业执照/法人身份证/历史合同等材料未插入文档。
根因：原 QUAL_CHAPTER_KEYWORDS 不覆盖用户实际章节标题「投标人基本资料」等。

These tests guard the contract:
1. 公司信息/资质证书 → 命中 QUAL 关键词的章节
2. 项目人员证书 → 命中 PERSONNEL 关键词的章节
3. 历史合同 → 命中 CONTRACT 关键词的章节
4. 兜底：以上都没命中时挂到最后一个章节（材料一定要出现）
"""
import re

from PIL import Image as PILImage

from app.services.materials_injection import (
    QUAL_CHAPTER_KEYWORDS,
    PERSONNEL_KEYWORDS,
    CONTRACT_CHAPTER_KEYWORDS,
    CONTRACT_PRIMARY_KEYWORDS,
    drop_already_embedded,
    inject_materials_into_chapters,
)


def _payload(titles: list[str]) -> tuple[list[dict], list[list[dict]]]:
    chapters = [{"title": t, "content": ""} for t in titles]
    images: list[list[dict]] = [[] for _ in chapters]
    return chapters, images


def _make_png(path, color=(200, 30, 30), size=(60, 40)):
    """真实 PNG 文件——内容去重要真的读盘算 md5."""
    path.parent.mkdir(parents=True, exist_ok=True)
    PILImage.new("RGB", size, color).save(path)
    return path


def _image_refs(content: str, images: list[dict]) -> list[str]:
    """一章里引用到的图片路径：正文 [IMG:] 标记 + chapter_images，两条通道合起来数."""
    return re.findall(r"\[IMG:([^|\]]*)\|", content or "") + [i["path"] for i in images]


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

        inject_materials_into_chapters(
            chapters=chapters,
            chapter_images=images,
            company_text_block="",
            qual_text_block="",
            all_qual_section_images=[],
            personnel_cert_images=[],
            contract_text_block=contract_block,
        )

        # 类似项目情况表 命中 CONTRACT → 文本 append（合同图随文本里的 [IMG:] 标记
        # 进正文，不再单独走 chapter_images）
        assert contract_block in chapters[0]["content"]
        assert images[0] == []
        # 「其他材料」是兜底章节，业绩正题已命中就不该再收一份
        assert contract_block not in chapters[1]["content"]

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

        inject_materials_into_chapters(
            chapters=chapters,
            chapter_images=images,
            company_text_block="",
            qual_text_block="",
            all_qual_section_images=[],
            personnel_cert_images=[],
            contract_text_block=contract_block,
        )

        assert contract_block in chapters[-1]["content"]
        assert images[-1] == []

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
        )

        # 注入到第一个匹配（投标人基本资料），不是最后一章
        assert chapters[0]["content"].startswith(company_block)
        assert chapters[-1]["content"] == ""

    def test_qual_and_contract_independent_fallback(self):
        """QUAL 命中但 CONTRACT 没命中 → 只有 CONTRACT 走 fallback."""
        chapters, images = _payload(["投标人基本资料", "服务方案"])
        contract_block = "## 公司业绩一览表"

        inject_materials_into_chapters(
            chapters=chapters,
            chapter_images=images,
            company_text_block="## 公司基本情况",
            qual_text_block="",
            all_qual_section_images=[],
            personnel_cert_images=[],
            contract_text_block=contract_block,
        )

        # QUAL 注入到第一章节（投标人基本资料）
        assert "## 公司基本情况" in chapters[0]["content"]
        # CONTRACT fallback 到最后一章节（服务方案）
        assert contract_block in chapters[-1]["content"]
        assert images[-1] == []

class TestContractInjectedOnce:
    """合同扫描件在一篇章节里只能被引用一次，且整块业绩表只落一个章节.

    用户反馈「合同图重复注入」。真实产物（玉溪大红山）里 8 张合同图变成了 48 处
    引用 = 3 个命中 CONTRACT 的章节 × (正文 [IMG:] 标记 8 + chapter_images 8)。
    两条通道装的是同一批图：contract_text_block 里每张合同都自带 [IMG:] 标记，
    而 chapter_images 又 extend 了一遍。
    """

    def test_contract_image_referenced_once_not_twice(self):
        chapters, images = _payload(["类似项目情况表"])
        block = "[IMG:/c1.jpg|A — 合同]\n[IMG:/c2.jpg|B — 合同]"

        inject_materials_into_chapters(
            chapters=chapters,
            chapter_images=images,
            company_text_block="",
            qual_text_block="",
            all_qual_section_images=[],
            personnel_cert_images=[],
            contract_text_block=block,
        )

        # 正文标记与 chapter_images 合起来，每张图恰好一次
        assert _image_refs(chapters[0]["content"], images[0]) == ["/c1.jpg", "/c2.jpg"]

    def test_contract_block_goes_to_the_performance_chapter_only(self):
        """「类似项目情况表」是业绩正题，「其他材料」是兜底——只该进前者."""
        chapters, images = _payload(
            ["商务文件其他材料", "类似项目情况表", "技术文件其他材料"]
        )
        block = "## 公司业绩一览表\n| 项目 | 金额 |"

        inject_materials_into_chapters(
            chapters=chapters,
            chapter_images=images,
            company_text_block="",
            qual_text_block="",
            all_qual_section_images=[],
            personnel_cert_images=[],
            contract_text_block=block,
        )

        holders = [ch["title"] for ch in chapters if block in ch["content"]]
        assert holders == ["类似项目情况表"]

    def test_contract_block_goes_to_generic_chapter_when_no_performance_chapter(self):
        chapters, images = _payload(["商务文件其他材料", "服务方案"])
        block = "## 公司业绩一览表"

        inject_materials_into_chapters(
            chapters=chapters,
            chapter_images=images,
            company_text_block="",
            qual_text_block="",
            all_qual_section_images=[],
            personnel_cert_images=[],
            contract_text_block=block,
        )

        assert block in chapters[0]["content"]
        assert block not in chapters[1]["content"]

    def test_primary_keywords_exclude_generic_buckets(self):
        for title in ("类似项目情况表", "公司业绩", "项目经验", "成功案例"):
            assert any(kw in title for kw in CONTRACT_PRIMARY_KEYWORDS), title
        for title in ("商务文件其他材料", "技术文件其他材料", "其他内容"):
            assert not any(kw in title for kw in CONTRACT_PRIMARY_KEYWORDS), title


class TestDropAlreadyEmbedded:
    """同一份材料在库里可能有两条存储路径，内容逐字节相同.

    真实数据：营业执照既是 company_profile.business_license_image
    （uploads/company/5e71….png），又是一条同名资质（ocr/0d24….png）——
    两个文件 md5 都是 4baee17e…。按路径字符串去重认不出，按内容才认得出，
    于是同一张执照在「投标人基本资料」里出现了两次。
    """

    def test_same_content_under_a_different_path_is_dropped(self, tmp_path):
        a = _make_png(tmp_path / "company" / "license.png")
        b = _make_png(tmp_path / "ocr" / "license.png")

        embedded: set[str] = set()
        first = [{"path": str(a), "label": "营业执照"}]
        second = [{"path": str(b), "label": "营业执照 — 530100100391096"}]

        assert drop_already_embedded(first, embedded) == first
        assert drop_already_embedded(second, embedded) == []

    def test_different_content_is_kept(self, tmp_path):
        a = _make_png(tmp_path / "company" / "license.png", color=(200, 30, 30))
        b = _make_png(tmp_path / "ocr" / "tax.png", color=(20, 120, 200))

        embedded: set[str] = set()
        drop_already_embedded([{"path": str(a), "label": "营业执照"}], embedded)

        keep = [{"path": str(b), "label": "税务登记证"}]
        assert drop_already_embedded(keep, embedded) == keep

    def test_the_returned_items_are_not_mutated(self, tmp_path):
        a = _make_png(tmp_path / "a.png")
        embedded: set[str] = set()
        item = {"path": str(a), "label": "营业执照"}
        drop_already_embedded([item], embedded)
        assert item == {"path": str(a), "label": "营业执照"}

    def test_unresolvable_paths_fall_back_to_the_path_itself(self):
        """文件找不到时不能一律当成同一张图丢掉——那样会丢材料."""
        embedded: set[str] = set()
        a = [{"path": "gone/a.png", "label": "x"}]
        b = [{"path": "gone/b.png", "label": "y"}]

        assert drop_already_embedded(a, embedded) == a
        assert drop_already_embedded(b, embedded) == b
        assert drop_already_embedded([{"path": "gone/a.png", "label": "z"}], embedded) == []


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
