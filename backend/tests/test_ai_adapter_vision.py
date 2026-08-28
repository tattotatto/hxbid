# -*- coding: utf-8 -*-
"""ai_adapter 视觉识别配置测试.

回归：视觉识别从 qwen(tongyi) 回退改为 DeepSeek 自身 vision 模型
（deepseek-v4-flash-vision-exp，同一 DeepSeek key）。改动 PROVIDERS
元数据或 config.py 时此文件校验三件事：
  1) deepseek 被标记为 vision-capable（不再回退 tongyi）；
  2) deepseek 的 vision_model 精确等于配置的 DEEPSEEK_VISION_MODEL；
  3) list_providers() 对 deepseek 暴露该模型且 configured 状态正确。
"""

from app.config import settings
from app.services import ai_adapter

VISION_EXPECTED = "deepseek-v4-flash-vision-exp"


def test_deepseek_marked_vision_capable():
    meta = ai_adapter.PROVIDERS["deepseek"]
    assert meta["vision"] is True
    assert ai_adapter.ai_adapter.supports_vision("deepseek") is True


def test_deepseek_vision_model_matches_config():
    meta = ai_adapter.PROVIDERS["deepseek"]
    assert meta["vision_model"] == settings.DEEPSEEK_VISION_MODEL
    assert meta["vision_model"] == VISION_EXPECTED


def test_list_providers_exposes_deepseek_vision():
    provs = {p["id"]: p for p in ai_adapter.ai_adapter.list_providers()}
    ds = provs["deepseek"]
    assert VISION_EXPECTED in ds["models"]
    assert ds["configured"] is bool(settings.DEEPSEEK_API_KEY)