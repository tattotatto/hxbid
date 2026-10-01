# -*- coding: utf-8 -*-
"""POST /company/ocr-existing 路径解析回归测试.

`_save_upload` 存进 DB 的是「相对 UPLOAD_DIR」的路径（`company/xxx.png`，供
/uploads/ 静态端用），但 /ocr-existing 曾直接 `Path(image_path).is_file()`——按
进程 cwd 解析。容器里 cwd=/app、UPLOAD_DIR=/app/uploads，于是去找 /app/company/
xxx.png，必然 404「Image not found: company/xxx.png」。

与 ocr_service 2026-08-28 那次（test_ocr_vision_path.py）同源：cwd 与 UPLOAD_DIR
分离后，任何「裸相对路径」读取都会踩空。生产回归：2026-10-01 上传营业执照点 OCR。
"""

import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api import company
from app.config import settings


def _call(payload, monkeypatch, upload_dir, captured, cwd):
    """以 monkeypatch 后的 UPLOAD_DIR 调用端点，返回结果或抛出的异常.

    cwd 必须**不是** UPLOAD_DIR 的父目录——否则裸相对路径恰好也能命中，
    测试就失去了区分力（生产是 cwd=/app、UPLOAD_DIR=/app/uploads，
    看似相邻但 `company/x.png` 与 `uploads/company/x.png` 是两条不同的路径）。
    """

    async def fake_analyze(file_bytes, filename, doc_type="business_license"):
        captured["file_bytes"] = file_bytes
        captured["filename"] = filename
        captured["doc_type"] = doc_type
        return {"company_name": "云南宏曦科技有限公司", "ocr_text": "ok"}

    cwd.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(upload_dir))
    monkeypatch.setattr(company, "analyze_document_image", fake_analyze)
    return asyncio.run(company.ocr_existing_image(data=payload, current_user=None))


class TestOcrExistingPathResolution:
    def test_relative_to_upload_dir_found_when_cwd_differs(
        self, tmp_path, monkeypatch
    ):
        """核心回归：DB 里存的 `company/x.png` 必须按 UPLOAD_DIR 解析，
        即使进程 cwd 指向别处（模拟容器 cwd=/app）。"""
        upload_dir = tmp_path / "uploads"
        (upload_dir / "company").mkdir(parents=True)
        img = upload_dir / "company" / "e037d341.png"
        img.write_bytes(b"business-license-bytes")

        captured = {}
        result = _call(
            {"image_path": "company/e037d341.png", "doc_type": "business_license"},
            monkeypatch,
            upload_dir,
            captured,
            cwd=tmp_path / "app",  # 模拟容器 cwd=/app
        )

        assert captured["file_bytes"] == b"business-license-bytes"
        assert captured["filename"] == "e037d341.png"
        assert captured["doc_type"] == "business_license"
        assert result["company_name"] == "云南宏曦科技有限公司"

    def test_legacy_uploads_prefix_still_resolves(self, tmp_path, monkeypatch):
        """旧约定（带 `uploads/` 前缀）也认，端点头注释里就是这么写的。"""
        upload_dir = tmp_path / "uploads"
        (upload_dir / "company").mkdir(parents=True)
        (upload_dir / "company" / "old.png").write_bytes(b"legacy-bytes")

        captured = {}
        _call(
            {"image_path": "uploads/company/old.png", "doc_type": "business_license"},
            monkeypatch,
            upload_dir,
            captured,
            cwd=tmp_path / "app",
        )

        assert captured["file_bytes"] == b"legacy-bytes"

    def test_missing_file_raises_404_with_path(self, tmp_path, monkeypatch):
        upload_dir = tmp_path / "uploads"
        upload_dir.mkdir(parents=True)

        with pytest.raises(HTTPException) as exc:
            _call(
                {"image_path": "company/nope.png"},
                monkeypatch,
                upload_dir,
                {},
                cwd=tmp_path / "app",
            )

        assert exc.value.status_code == 404
        assert "company/nope.png" in exc.value.detail

    def test_empty_path_raises_400(self, tmp_path, monkeypatch):
        with pytest.raises(HTTPException) as exc:
            _call(
                {"image_path": ""},
                monkeypatch,
                tmp_path / "uploads",
                {},
                cwd=tmp_path / "app",
            )

        assert exc.value.status_code == 400

    def test_path_outside_upload_dir_rejected(self, tmp_path, monkeypatch):
        """防目录穿越：UPLOAD_DIR 之外的文件不得被读取（否则等于任意文件读）。"""
        upload_dir = tmp_path / "uploads"
        upload_dir.mkdir(parents=True)
        secret = tmp_path / "secret.txt"
        secret.write_bytes(b"SECRET")

        for probe in (str(secret), "../secret.txt"):
            with pytest.raises(HTTPException) as exc:
                _call(
                    {"image_path": probe},
                    monkeypatch,
                    upload_dir,
                    {},
                    cwd=tmp_path / "app",
                )

            assert exc.value.status_code == 404, probe
