# -*- coding: utf-8 -*-
"""ocr_service 路径解析回归测试.

save_ocr_image 返回「相对 UPLOAD_DIR」的路径（如 `ocr/xxx.png`），给 /uploads/
静态端用；但 analyze_document_image 读取时必须按 UPLOAD_DIR 前缀解析成绝对路径
再交给 vision / tesseract——否则容器（cwd=/app, UPLOAD_DIR=/app/uploads）里
FileNotFoundError，vision 静默失败落空 → 前端「自动识别未提取到文字」。

回归起点：2026-08-28 生产 OCR 首次实战暴露（docker 部署后 cwd 与 UPLOAD_DIR 分离）。
"""

import asyncio
from pathlib import Path

import pytest

from app.services import ocr_service
from app.services.ocr_service import analyze_document_image


class TestReadPathResolution:
    def test_analyze_reads_absolute_path_returns_relative(self, tmp_path, monkeypatch):
        """读取传给 vision/tesseract 的是 UPLOAD_DIR 拼接后的绝对路径；
        返回给前端的 image_path 仍是相对 UPLOAD_DIR 路径。"""
        upload_dir = tmp_path / "uploads"
        ocr_dir = upload_dir / "ocr"
        ocr_dir.mkdir(parents=True)
        img = ocr_dir / "abc.png"
        img.write_bytes(b"fake-image-bytes")

        monkeypatch.setattr(ocr_service, "UPLOAD_DIR", upload_dir)

        seen = {}

        async def fake_vision(path, doc_type):
            seen["vision_path"] = path
            return None

        def fake_tess(path):
            seen["tess_path"] = path
            return None

        monkeypatch.setattr(ocr_service, "_extract_with_vision", fake_vision)
        monkeypatch.setattr(ocr_service, "extract_text_from_image", fake_tess)

        result = asyncio.run(analyze_document_image(file_bytes=b"x", filename="abc.png"))

        vp = Path(seen["vision_path"])
        assert vp.is_absolute()
        assert vp.parent == ocr_dir  # 落在 UPLOAD_DIR/ocr 下（save 的真实落盘处）
        # 核心不变式：读取路径 == UPLOAD_DIR + 返回给前端的相对路径，
        # 二者一致才不 FileNotFoundError（回归点）
        assert seen["vision_path"] == str(upload_dir / result["image_path"])
        assert Path(result["image_path"]).parent.name == "ocr"  # 相对 UPLOAD_DIR，前端可存 /uploads/
        assert not Path(result["image_path"]).is_absolute()
        assert seen["tess_path"] == seen["vision_path"]

    def test_absolute_path_from_save_passed_through(self, tmp_path, monkeypatch):
        """save_ocr_image 若已返回绝对路径则直通，不做二次前缀拼接。"""
        monkeypatch.setattr(ocr_service, "UPLOAD_DIR", tmp_path / "uploads")
        abs_img = str(tmp_path / "uploads" / "ocr" / "x.png")

        monkeypatch.setattr(ocr_service, "save_ocr_image", lambda *a, **k: abs_img)

        seen = {}

        async def fake_vision(path, doc_type):
            seen["vision_path"] = path
            return None

        def fake_tess(path):
            seen["tess_path"] = path
            return None

        monkeypatch.setattr(ocr_service, "_extract_with_vision", fake_vision)
        monkeypatch.setattr(ocr_service, "extract_text_from_image", fake_tess)

        asyncio.run(analyze_document_image(file_bytes=b"x", filename="x.png"))

        assert seen["vision_path"] == abs_img
        assert seen["tess_path"] == abs_img