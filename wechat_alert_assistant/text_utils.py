from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def compact_text(text: str) -> str:
    return re.sub(r"\s+", "", normalize_text(text))


def similarity(left: str, right: str) -> float:
    left = compact_text(left)
    right = compact_text(right)
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def contains_fuzzy(haystack: str, needle: str, threshold: float) -> bool:
    haystack_c = compact_text(haystack)
    needle_c = compact_text(needle)
    if not needle_c:
        return False
    if needle_c in haystack_c:
        return True
    if len(haystack_c) < len(needle_c):
        return similarity(haystack_c, needle_c) >= threshold
    window = len(needle_c)
    for index in range(0, len(haystack_c) - window + 1):
        if similarity(haystack_c[index : index + window], needle_c) >= threshold:
            return True
    return False

