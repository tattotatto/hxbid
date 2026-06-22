# Task 19 Completion Report — 通知服务 (企业微信 + 钉钉)

**Date:** 2026-06-22
**Status:** Complete

## Summary

Implemented a notification service that sends messages via WeCom (企业微信) and DingTalk (钉钉) webhook bots for key system events. The service is hooked into the generation pipeline to notify users when bid document generation is complete.

## Files Changed

### Created
- `backend/app/services/notification.py` — Notification service with four event types and webhook senders

### Modified
- `backend/app/api/bid.py` — Added import, project_name snapshot, and notification call after generation completes

## Details

### Notification Events
Four event templates are defined:
| Event | Title | Description |
|---|---|---|
| `generation_complete` | 标书生成完成 | Fires when all chapters are generated and project enters "review" status |
| `review_reminder` | 审核待办提醒 | For future scheduler: projects >24h unreviewed |
| `deadline_warning` | 投标截止预警 | For future scheduler: 3 days before bid deadline |
| `system_error` | 系统异常告警 | For future error handler integration |

### Webhook Channels
- **WeCom (企业微信):** markdown message type via `WECOM_WEBHOOK_URL` setting
- **DingTalk (钉钉):** markdown message type via `DINGTALK_WEBHOOK_URL` setting
- Both are silent on failure (best-effort, non-blocking)
- 10-second timeout per webhook call

### Hook Location
The `generation_complete` notification fires in the SSE event generator (`backend/app/api/bid.py`), immediately after the project status is set to `"review"` — after all chapters have been generated and committed. The `project_name` is snapshotted before the async generator runs to avoid session-closure issues.

## Configuration

Two environment variables (already present in `config.py`):
- `WECOM_WEBHOOK_URL` — defaults to `""` (disabled)
- `DINGTALK_WEBHOOK_URL` — defaults to `""` (disabled)

When both are empty, `send_notification` is a no-op.
