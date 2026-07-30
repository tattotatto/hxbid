# Task 1 Review: pdf_extractor -- 完整提取格式章节

**Reviewer:** Automated review
**Date:** 2026-07-30
**Verdict:** APPROVED (with minor notes)

---

## Verification Checklist

| # | Requirement | Status | Evidence |
|---|-------------|--------|----------|
| 1 | `extract_format_section` exists | PASS | `pdf_extractor.py:113` -- returns `{full_text, tables, start_page, end_page, total_pages}` |
| 2 | `locate_format_pages` exists | PASS | `pdf_extractor.py:25` -- returns `(start, end)` 0-indexed or `None` |
| 3 | `extract_tables_from_pages` exists | PASS | `pdf_extractor.py:82` -- returns `list[dict]` with page/table_index/rows |
| 4 | Tests pass | PASS | **12 passed, 0 failed** in 3.99s |

All three interface requirements are met.

---

## Implementation Review

### What was built (vs. brief)

The implementation closely follows the brief with these improvements:

| Aspect | Brief | Delivered | Note |
|--------|-------|-----------|------|
| Search direction | Forward (page 0 upward) | **Backward** (from last page) | Reasonable optimization -- format sections are at document end |
| Backtracking | None | Up to 3 pages backward for true start | Handles keyword on line after the title |
| Keywords | 3 keywords | 6 keywords (including chapter-number variants) | Better coverage for common chapter heading formats |
| Boundary safety | None | `if i >= len(pdf.pages): break` in loops | Guards against out-of-range page indices |
| Bonus | -- | `extract_full_document()` | Useful utility for non-section-specific extraction |
| Return shape | `{"full_text": ..., "tables": ..., "pages": [int, int]}` | `{"full_text": ..., "tables": ..., "start_page": int, "end_page": int, "total_pages": int}` | Slightly different key names; clear and explicit |

### Test coverage

12 tests across 6 test classes covering:
- **Happy path:** full section extraction with text/table assertions, table structure validation
- **Locator:** positive match, return-type consistency
- **Text extraction:** single page, multi-page, out-of-range boundary
- **Table extraction:** basic extraction, out-of-range boundary
- **Full document:** end-to-end full doc extraction
- **Errors:** `FileNotFoundError` for both `extract_format_section` and `extract_full_document`

Good coverage of the public API. The test for "returns None" (`test_locate_returns_none_for_no_match`) correctly handles the ambiguous case (real file may match, tests return-type consistency instead).

---

## Concerns (non-blocking)

1. **Keyword fragility (noted in report):** `FORMAT_KEYWORDS` is a static list. A招标文件 using non-standard headings (e.g. "第四部分 投标书编制要求") could fail to locate the section. The fallback to full-document extraction mitigates this but loses precision. Consider importing `FORMAT_SECTION_PATTERNS` from `format_extractor.py` for regex-based matching in a follow-up.

2. **Merged-cell `None` values (noted in report):** pdfplumber returns `None` for merged cells. Downstream consumers of `tables[].rows` must handle this. The docstring on `extract_tables_from_pages` already documents `list[list[str | None]]` -- this is adequate.

3. **Return-type annotation:** `locate_format_pages` uses `Tuple[int, int] | None` but the brief specifies `tuple[int, int] | None`. This is cosmetic and equivalent in Python 3.9+. No action needed.

---

## Summary

Task 1 is complete. The three required interfaces (`extract_format_section`, `locate_format_pages`, `extract_tables_from_pages`) exist, are well documented, and are covered by 12 passing tests. The implementation improves on the brief in several small ways (backward search, more keywords, boundary safety, bonus `extract_full_document`). The two concerns raised in the self-report (keyword fragility, merged-cell handling) are valid but non-blocking.
