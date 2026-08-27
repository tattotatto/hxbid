"""bid_projects 评分指标/评分报告列 + alembic 0007 迁移链 单元测试.
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""
from pathlib import Path

from app.models.project import BidProject

BACKEND_DIR = Path(__file__).resolve().parent.parent


def test_bid_project_has_scoring_columns():
    table = BidProject.__table__
    assert "scoring_rubric_json" in table.c
    assert "scoring_report_json" in table.c


def test_scoring_columns_default_to_empty_json_object():
    table = BidProject.__table__
    assert table.c.scoring_rubric_json.default.arg == "{}"
    assert table.c.scoring_report_json.default.arg == "{}"


def test_migration_head_is_0007():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    script = ScriptDirectory.from_config(cfg)
    assert set(script.get_heads()) == {"0007"}