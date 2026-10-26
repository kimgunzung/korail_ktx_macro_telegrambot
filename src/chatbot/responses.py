"""Transport-neutral chatbot reply (rendered by the Telegram runner)."""
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Tuple


@dataclass
class Reply:
    """One bot answer: text plus optional quick-reply buttons (label, message_text)."""
    text: str
    quick_replies: List[Tuple[str, str]] = field(default_factory=list)
    prefix_lines: List[str] = field(default_factory=list)  # error lines shown above ``text``

    @property
    def full_text(self) -> str:
        return "\n".join(self.prefix_lines + [self.text]) if self.prefix_lines else self.text


def text_response(text: str, quick_replies: Optional[Iterable] = None) -> Reply:
    replies: List[Tuple[str, str]] = []
    for item in quick_replies or []:
        if isinstance(item, tuple):
            replies.append((item[0], item[1]))
        else:
            replies.append((item, item))
    return Reply(text=text.strip(), quick_replies=replies)
