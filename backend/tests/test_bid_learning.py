"""bid_learning 纯函数测试:对齐 chunk 构建与截断。"""

from app.services.bid_learning import _build_alignment_chunks, _truncate


def test_truncate_respects_limit():
    text = "a" * 5000
    assert len(_truncate(text, 1000)) == 1000


def test_build_alignment_chunks_skips_missing():
    lesson = {
        "requirements_coverage": [
            {
                "requirement": "须有保安服务许可证",
                "category": "qualification",
                "bid_response": "我们持有保安服务许可证",
                "quality": "good",
                "lesson": "把资质放在资格审查醒目位置",
            },
            {"requirement": "须提供近三年业绩", "category": "qualification", "bid_response": "", "quality": "missing", "lesson": ""},
        ]
    }
    chunks = _build_alignment_chunks("p1", "某项目标书", lesson)
    assert len(chunks) == 1
    c = chunks[0]
    assert c["metadata"]["source"] == "lesson"
    assert c["metadata"]["pair_id"] == "p1"
    assert "保安服务许可证" in c["content"]
    assert c["title"].startswith("招标要求：")
