"""宏曦标书 - 信息搜集页的「目录附件章节」行 单元测试.

用户需求（2026-09-27）：「只要在目录中设置为附件（资料选择）的章节，下一步
都在资质与证件中给用户选择，包含上传和从资源库选择」。

现状缺口：目录里设成 attachment 的章节，对信息搜集这一步**零影响** ——
「资质与证件」区块的行完全由招标解析出的 required_documents 决定。

**两套东西刻意不合并**（用户裁定）：
  - 需求项行：来自 parsed_requirements_json["required_documents"]
    → 选择写 ProjectQualification / ProjectContract 关联表
  - 附件章节行：来自 ProjectChapter.chapter_type == "attachment"
    → 选择写 ProjectChapter.chapter_meta_json["attachments"]（生成期只认它）
只在本页并排展示，各写各的存储。
"""

import json

import pytest

from app.services.collection import _chapter_attachment_rows


class _Chapter:
    def __init__(self, cid, title, ctype, order=0, meta=None):
        self.id = cid
        self.title = title
        self.chapter_type = ctype
        self.order_index = order
        self.chapter_meta_json = json.dumps(meta or {}, ensure_ascii=False)


class TestChapterAttachmentRows:
    def test_only_attachment_chapters_are_returned(self):
        chapters = [
            _Chapter("c0", "一、封面", "fixed_form", 0),
            _Chapter("c1", "八、投标人基本资料", "attachment", 1),
            _Chapter("c2", "六、开标一览表", "table", 2),
            _Chapter("c3", "十四、服务方案", "ai_generated", 3),
        ]
        rows = _chapter_attachment_rows(chapters)
        assert [r["chapter_id"] for r in rows] == ["c1"]
        assert rows[0]["title"] == "八、投标人基本资料"

    def test_attachments_are_read_from_chapter_meta(self):
        att = [{"kind": "qualification", "id": "q1", "label": "营业执照",
                "path": "ocr/a.png"}]
        rows = _chapter_attachment_rows(
            [_Chapter("c1", "附件", "attachment", meta={"attachments": att})])
        assert rows[0]["attachments"] == att

    def test_missing_or_broken_meta_yields_empty_list(self):
        """老项目 / 脏数据不能把整页搞崩."""
        broken = _Chapter("c1", "附件", "attachment")
        broken.chapter_meta_json = "{不是 JSON"
        rows = _chapter_attachment_rows([broken, _Chapter("c2", "其他", "attachment")])
        assert [r["attachments"] for r in rows] == [[], []]

    def test_sorted_by_order_index(self):
        rows = _chapter_attachment_rows([
            _Chapter("c2", "后", "attachment", 5),
            _Chapter("c1", "前", "attachment", 1),
        ])
        assert [r["title"] for r in rows] == ["前", "后"]

    def test_no_attachment_chapters_yields_empty(self):
        assert _chapter_attachment_rows([_Chapter("c0", "封面", "fixed_form")]) == []

    def test_empty_project_yields_empty(self):
        assert _chapter_attachment_rows([]) == []

    def test_rows_carry_a_marker_so_frontend_can_tell_them_apart(self):
        """前端要能区分"招标需求项行"与"目录附件行"（决定调哪套端点）."""
        rows = _chapter_attachment_rows([_Chapter("c1", "附件", "attachment")])
        assert rows[0]["source"] == "chapter_attachment"


class TestSetChapterAttachments:
    """替换某章节的附件清单 —— 信息搜集页挂/摘材料走这里.

    复用目录页那套清洗口径（`_prune_attachments`：路径越界 / 资源库行已删 /
    跨节点内容重复），保证两处入口写出来的数据形状与生成期消费的一致。
    """

    class _FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return self

        def all(self):
            return self._rows

    class _FakeDB:
        async def execute(self, _stmt):
            # 资源库查询一律返回空 = 引用的行已删
            return TestSetChapterAttachments._FakeResult([])

    @pytest.mark.asyncio
    async def test_writes_into_chapter_meta_json(self):
        from app.services.chapter_attachments import set_chapter_attachments

        ch = _Chapter("c1", "附件", "attachment")
        att = [{"kind": "upload", "id": "u1", "label": "凭证", "path": "project_p1/a.png"}]
        kept, pruned = await set_chapter_attachments(ch, att, self._FakeDB())
        assert kept == att
        assert pruned == []
        assert json.loads(ch.chapter_meta_json)["attachments"] == att

    @pytest.mark.asyncio
    async def test_preserves_other_meta_keys(self):
        """只改 attachments，别把 match / table_columns 等一起冲掉."""
        from app.services.chapter_attachments import set_chapter_attachments

        ch = _Chapter("c1", "附件", "attachment",
                      meta={"match": {"status": "na"}, "table_columns": ["序号"]})
        await set_chapter_attachments(ch, [], self._FakeDB())
        meta = json.loads(ch.chapter_meta_json)
        assert meta["match"] == {"status": "na"}
        assert meta["table_columns"] == ["序号"]
        assert meta["attachments"] == []

    @pytest.mark.asyncio
    async def test_broken_meta_is_recovered_not_crashed(self):
        from app.services.chapter_attachments import set_chapter_attachments

        ch = _Chapter("c1", "附件", "attachment")
        ch.chapter_meta_json = "{不是 JSON"
        kept, _ = await set_chapter_attachments(ch, [], self._FakeDB())
        assert kept == []
        assert json.loads(ch.chapter_meta_json)["attachments"] == []

    @pytest.mark.asyncio
    async def test_deleted_library_row_is_pruned(self):
        from app.services.chapter_attachments import set_chapter_attachments

        ch = _Chapter("c1", "附件", "attachment")
        att = [{"kind": "qualification", "id": "gone", "label": "已删的资质",
                "path": "ocr/x.png"}]
        kept, pruned = await set_chapter_attachments(ch, att, self._FakeDB())
        assert kept == []
        assert pruned == ["已删的资质"]

    @pytest.mark.asyncio
    async def test_path_escape_is_pruned(self):
        from app.services.chapter_attachments import set_chapter_attachments

        ch = _Chapter("c1", "附件", "attachment")
        att = [{"kind": "upload", "id": "u1", "label": "越界", "path": "../../etc/passwd"}]
        kept, pruned = await set_chapter_attachments(ch, att, self._FakeDB())
        assert kept == []
        assert pruned == ["越界"]
