# Task 11 Report: AI Pipeline Engine

**Date:** 2026-06-22
**Status:** Complete

## Changes Made

### `backend/app/services/ai_pipeline.py` (new)

Created the core AI orchestration module with the following components:

1. **SYSTEM_PROMPT** constant — system-level instructions for the AI, enforcing Chinese bid-writing standards with 5 specific writing rules and a copyright notice.

2. **`parse_bid_requirements(document_text: str) -> dict`** — Sends truncated document text (max 15000 chars) to the AI with `response_format={"type": "json_object"}` and `temperature=0.3`. Extracts structured fields: `project_name`, `project_budget`, `project_duration`, `qualification_requirements`, `personnel_requirements`, `service_requirements`, `evaluation_criteria`, `special_requirements`, `bid_sections`. Falls back to safe defaults on JSON parse failure.

3. **`generate_outline(requirements: dict) -> list[dict]`** — Returns a list of `{"order_index": int, "title": str}` dicts. Uses `bid_sections` from parsed requirements if available; otherwise falls back to 11 default security bid sections.

4. **`generate_chapter(chapter_title, requirements, context="", stream=True) -> AsyncIterator[str] | str`** — Builds a prompt from chapter title + requirements summary + additional context. Delegates to `ai_adapter.chat_completion_stream` (streaming) or `ai_adapter.chat_completion` (non-streaming) based on the `stream` parameter.

5. **`generate_chapter_with_materials(chapter_title, requirements, matched_qualifications, matched_personnel, similar_chapters) -> AsyncIterator[str]`** — Assembles a rich context from matched qualifications (with cert numbers), de-identified personnel profiles, and truncated historical chapters (3000 chars each). Always streams output.

### Key Design Rules Applied

- All AI calls go through `ai_adapter` singleton (no direct API calls)
- `SYSTEM_PROMPT` is prepended to every AI call via `_build_messages()`
- Personnel names and ID numbers are de-identified via `deidentify_text()` before entering prompt context
- Supports both SQLAlchemy model instances and plain dicts for materials

### Verification

```
> python -c "from app.services.ai_pipeline import SYSTEM_PROMPT, parse_bid_requirements, generate_outline; print('Pipeline OK'); print('Prompt length:', len(SYSTEM_PROMPT))"
Pipeline OK
Prompt length: 274
```

## Files Modified

- `backend/app/services/ai_pipeline.py` — new file

## Dependencies

- `app.services.ai_adapter` (ai_adapter singleton)
- `app.services.deid` (deidentify_text)
