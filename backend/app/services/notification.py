"""宏曦标书 - Notification Service.

Sends notifications via WeCom (企业微信) and/or DingTalk (钉钉) webhook bots
for key system events (generation complete, review reminders, deadline warnings,
system errors).

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import httpx
from app.config import settings

EVENT_TEMPLATES = {
    "generation_complete": {
        "title": "标书生成完成",
        "content": "「{project_name}」标书已生成，请前往审核。",
    },
    "review_reminder": {
        "title": "审核待办提醒",
        "content": "「{project_name}」标书超过24小时未审核，请及时处理。",
    },
    "deadline_warning": {
        "title": "投标截止预警",
        "content": "「{project_name}」距离投标截止还有3天。",
    },
    "system_error": {
        "title": "系统异常告警",
        "content": "宏曦标书系统发生异常：{error_detail}",
    },
}

async def send_notification(event_type: str, **kwargs) -> None:
    """Send notification to configured channels. Non-blocking, best-effort."""
    template = EVENT_TEMPLATES.get(event_type)
    if not template:
        return

    content = template["content"].format(**kwargs)
    title = template["title"]

    if settings.WECOM_WEBHOOK_URL:
        await _send_wecom(title, content)

    if settings.DINGTALK_WEBHOOK_URL:
        await _send_dingtalk(title, content)

async def _send_wecom(title: str, content: str) -> None:
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                settings.WECOM_WEBHOOK_URL,
                json={
                    "msgtype": "markdown",
                    "markdown": {"content": f"## {title}\n{content}"},
                },
                timeout=10,
            )
    except Exception:
        pass  # Notification failures don't break main flow

async def _send_dingtalk(title: str, content: str) -> None:
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                settings.DINGTALK_WEBHOOK_URL,
                json={
                    "msgtype": "markdown",
                    "markdown": {"title": title, "text": f"## {title}\n{content}"},
                },
                timeout=10,
            )
    except Exception:
        pass
