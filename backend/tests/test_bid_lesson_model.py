"""BidLesson 模型列定义冒烟测试(无需 DB,只校验元数据)。"""

from app.models.bid_lesson import BidLesson


def test_bid_lesson_required_columns():
    cols = {c.name for c in BidLesson.__table__.columns}
    expected = {
        "id", "name", "source_type", "project_id",
        "tender_path", "bid_path", "status", "error",
        "lesson_json", "created_by", "created_at", "updated_at",
    }
    assert expected.issubset(cols)


def test_bid_lesson_defaults():
    assert BidLesson.__table__.c.status.default.arg == "pending"
    assert BidLesson.__table__.c.source_type.default.arg == "upload"
