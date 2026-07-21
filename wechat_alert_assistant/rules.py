from __future__ import annotations

from dataclasses import dataclass

from .config import RulesConfig
from .models import Message
from .text_utils import compact_text, contains_fuzzy, normalize_text


@dataclass(slots=True)
class MatchResult:
    matched: bool
    reason: str = ""


def parse_message(raw_text: str) -> Message:
    normalized = normalize_text(raw_text)
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    if not lines:
        return Message(chat_name="", sender="", content="", raw_text=raw_text)

    chat_name = lines[0]
    rest = " ".join(lines[1:]).strip()
    if not rest and len(lines) == 1:
        rest = lines[0]

    sender = ""
    content = rest
    for sep in ("：", ":"):
        if sep in rest:
            sender, content = rest.split(sep, 1)
            sender = sender.strip()
            content = content.strip()
            break

    return Message(chat_name=chat_name, sender=sender, content=content, raw_text=normalized)


def is_monitored_chat(message: Message, rules: RulesConfig) -> bool:
    source = f"{message.chat_name}\n{message.raw_text}"
    return any(contains_fuzzy(source, chat, rules.fuzzy_chat_threshold) for chat in rules.monitored_chats if chat.strip())


def match_rules(message: Message, rules: RulesConfig) -> MatchResult:
    raw = compact_text(message.raw_text)
    monitored = is_monitored_chat(message, rules)

    has_at_me = any(
        contains_fuzzy(raw, name, rules.fuzzy_name_threshold)
        for name in rules.my_names
        if name.strip()
    )
    has_keyword = any(compact_text(keyword) in raw for keyword in rules.keywords if keyword.strip())

    if not monitored and not (rules.allow_keyword_without_chat and has_keyword):
        return MatchResult(False)
    if has_at_me:
        return MatchResult(True, "@me")
    if has_keyword:
        return MatchResult(True, "keyword")
    return MatchResult(False)


def match_region_rules(message: Message, rules: RulesConfig, require_chat_match: bool = False) -> MatchResult:
    raw = compact_text(message.raw_text)
    if require_chat_match and not is_monitored_chat(message, rules):
        return MatchResult(False)

    if any(
        contains_fuzzy(raw, name, rules.fuzzy_name_threshold)
        for name in rules.my_names
        if name.strip()
    ):
        return MatchResult(True, "@me")
    if any(compact_text(keyword) in raw for keyword in rules.keywords if keyword.strip()):
        return MatchResult(True, "keyword")
    return MatchResult(False)


def matched_rule_items(message: Message, rules: RulesConfig) -> list[str]:
    raw = compact_text(message.raw_text)
    result = [
        name
        for name in rules.my_names
        if name.strip() and contains_fuzzy(raw, name, rules.fuzzy_name_threshold)
    ]
    result.extend(
        keyword
        for keyword in rules.keywords
        if keyword.strip() and compact_text(keyword) in raw
    )
    return result
