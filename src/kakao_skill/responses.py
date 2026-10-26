"""Kakao i Open Builder SkillResponse (v2.0) builders."""
from typing import Iterable, List, Optional

SIMPLE_TEXT_LIMIT = 1000   # characters per simpleText
MAX_OUTPUTS = 3
MAX_QUICK_REPLIES = 10


def _chunks(text: str, limit: int = SIMPLE_TEXT_LIMIT) -> List[str]:
    text = text.strip()
    if len(text) <= limit:
        return [text]
    parts: List[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        parts.append(remaining)
    return parts


def quick_reply(label: str, message: Optional[str] = None) -> dict:
    return {"label": label[:14], "action": "message", "messageText": message or label}


def text_response(text: str, quick_replies: Optional[Iterable] = None) -> dict:
    """A SkillResponse with one or more simpleText outputs and optional quick replies."""
    outputs = [{"simpleText": {"text": part}} for part in _chunks(text)[:MAX_OUTPUTS]]
    template: dict = {"outputs": outputs}
    replies = []
    for item in quick_replies or []:
        replies.append(quick_reply(*item) if isinstance(item, tuple) else quick_reply(item))
    if replies:
        template["quickReplies"] = replies[:MAX_QUICK_REPLIES]
    return {"version": "2.0", "template": template}


def utterance_of(payload: dict) -> str:
    try:
        return str(payload["userRequest"]["utterance"]).strip()
    except (KeyError, TypeError):
        return ""


def user_key_of(payload: dict) -> Optional[str]:
    try:
        key = payload["userRequest"]["user"]["id"]
    except (KeyError, TypeError):
        return None
    return str(key) if key else None
