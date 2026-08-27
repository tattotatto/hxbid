"""评分指标驱动目录补全（Feature A 纯函数）单元测试.
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""
from app.services.rubric_gap import gap_detect, build_rubric_nodes, attach_key_for


def _rubric():
    return {"items": [
        {"id": "t1", "dimension": "技术部分", "name": "服务方案", "kind": "content",
         "points": 15, "criteria": "…", "key_terms": ["服务方案"]},
        {"id": "t2", "dimension": "技术部分", "name": "针对性", "kind": "quality",
         "points": 5, "criteria": "…", "key_terms": ["针对性"]},
        {"id": "p1", "dimension": "报价", "name": "报价合理性", "kind": "price",
         "points": 30, "criteria": "…", "key_terms": ["报价"]},
        {"id": "p2", "dimension": "业绩", "name": "类似项目业绩", "kind": "performance",
         "points": 10, "criteria": "…", "key_terms": ["业绩", "类似项目"]},
    ]}


def _item(name):
    """构造单条 §4.1 形态的内容型指标（gap_detect/build_rubric_nodes 直接消费 missing 项）."""
    return {"id": "x", "dimension": "技术部分", "name": name, "kind": "content",
            "points": 10, "criteria": "…", "key_terms": [name]}


class TestGapDetect:
    def test_quality_and_price_kinds_not_in_missing(self):
        missing = gap_detect(_rubric(), ["技术部分", "报价", "投标函"])
        assert len(missing) == 2  # 只有 content 的 t1 + performance 的 p2

    def test_hit_by_key_term_in_title(self):
        missing = gap_detect(_rubric(), ["技术部分", "类似项目业绩", "投标函"])
        ids = {m["item"]["id"] for m in missing}
        assert "p2" not in ids  # 标题命中 key_term「类似项目」→ 不算缺失
        assert "t1" in ids

    def test_empty_rubric_no_missing(self):
        assert gap_detect({"items": []}, ["技术部分"]) == []


class TestBuildRubricNodes:
    def test_attaches_to_existing_dimension_chapter(self):
        # 注意：标题列表必须同时覆盖 performance 项 p2（业绩），否则它会以
        # 「业绩」维度新建顶层，与本用例的断言（只测 技术部分 挂载）冲突。
        missing = gap_detect(_rubric(), ["技术部分", "类似项目业绩", "投标函"])
        new_top, attach, added = build_rubric_nodes(missing, ["技术部分", "类似项目业绩", "投标函"])
        assert new_top == []  # 技术部分已存在 → 不建新顶层
        assert list(attach.keys()) == ["技术部分"]
        assert attach["技术部分"][0]["source"] == "scoring_rubric"
        assert attach["技术部分"][0]["rubric_item_id"] == "t1"
        assert added == ["服务方案"]

    def test_creates_top_level_when_dimension_missing(self):
        # 标题需覆盖 content 项 t1（服务方案）以免它作为缺失项参与，只留 p2 缺失。
        missing = gap_detect(_rubric(), ["投标函", "服务方案"])
        new_top, attach, added = build_rubric_nodes(missing, ["投标函", "服务方案"])
        # 业绩维度无匹配章节 → 新建顶层「业绩」
        assert len(new_top) == 1
        assert new_top[0]["title"] == "业绩"
        assert new_top[0]["source"] == "scoring_rubric"
        assert len(new_top[0]["children"]) == 1
        assert added == ["类似项目业绩"]
        assert attach == {}

    def test_child_only_dimension_is_top_level_when_only_child_matches(self):
        # 维度名只出现在子标题时（调用方只传顶层标题），应新建顶层而非 attach
        missing = [{"dimension": "技术部分", "item": _item("服务方案")}]
        new_top, attach, added = build_rubric_nodes(missing, ["商务标"])
        assert attach == {}
        assert len(new_top) == 1 and new_top[0]["title"] == "技术部分"
        assert added == ["服务方案"]

    def test_dimension_in_top_title_attaches(self):
        # 维度名出现在顶层标题时正常挂接（顶层标题带序号前缀也能匹配）
        missing = [{"dimension": "技术部分", "item": _item("服务方案")}]
        new_top, attach, added = build_rubric_nodes(missing, ["三、技术部分"])
        assert new_top == []
        assert attach.get("技术部分") is not None
        assert added == ["服务方案"]


class TestAttachKeyFor:
    def test_matches_dimension_in_prefixed_title(self):
        # 章节标题带序号前缀（「三、技术部分」）→ 命中维度 key
        assert attach_key_for("三、技术部分", {"技术部分": []}) == "技术部分"

    def test_no_match_returns_none(self):
        assert attach_key_for("投标函", {"技术部分": []}) is None

    def test_empty_attach_returns_none(self):
        assert attach_key_for("技术部分", {}) is None