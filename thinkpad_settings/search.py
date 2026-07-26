"""Fuzzy matching for the settings search.

Substring matching is a poor fit for these names. Nobody types
"VirtualizationTechnology"; they type "virt", "vt-x" or "kvm". A subsequence
match finds "VTdFeature" from "vtd" and "KeyboardBeep" from "kbbeep", and the
scoring pushes the obvious answer to the top rather than burying it among
incidental hits in some other setting's description.
"""

from __future__ import annotations

# An exact substring is always a better match than a scattered subsequence, so
# the two scoring paths live in clearly separated bands.
_SUBSTRING_BASE = 1000
_SUBSEQUENCE_BASE = 100

_WORD_START_BONUS = 60
_CONSECUTIVE_BONUS = 12
_SUBSEQ_WORD_START_BONUS = 18


def _is_boundary(text: str, index: int) -> bool:
    """True if *index* begins a word.

    CamelCase counts: the "B" of KeyboardBeep starts a word even though the
    character before it is a letter, which is what lets "beep" rank highly
    against an attribute name with no separators in it.
    """
    if index == 0:
        return True
    previous = text[index - 1]
    if not previous.isalnum():
        return True
    return text[index].isupper() and not previous.isupper()


def score(query: str, text: str) -> int | None:
    """How well *text* matches *query*. Higher is better; None means no match.

    Case-insensitive, but the original text is used for boundary detection so
    CamelCase still counts as word starts.
    """
    if not query:
        return 0
    if not text:
        return None

    lowered_query = query.lower()
    lowered_text = text.lower()

    position = lowered_text.find(lowered_query)
    if position >= 0:
        result = _SUBSTRING_BASE - min(position, 99)
        if _is_boundary(text, position):
            result += _WORD_START_BONUS
        if len(lowered_query) == len(lowered_text):
            result += _WORD_START_BONUS
        return result

    # Fall back to a subsequence: every character of the query must appear in
    # order, though not adjacently.
    positions: list[int] = []
    cursor = 0
    for char in lowered_query:
        found = lowered_text.find(char, cursor)
        if found < 0:
            return None
        positions.append(found)
        cursor = found + 1

    result = _SUBSEQUENCE_BASE
    for index, position in enumerate(positions):
        if _is_boundary(text, position):
            result += _SUBSEQ_WORD_START_BONUS
        if index and position == positions[index - 1] + 1:
            result += _CONSECUTIVE_BONUS

    # Matches spread across the whole string are weaker than tight ones.
    span = positions[-1] - positions[0] + 1
    result -= min(span - len(lowered_query), 40)
    return max(result, 1)


def score_fields(query: str, fields: list[tuple[str, int]]) -> int | None:
    """Best weighted score across several fields.

    *fields* is (text, weight). A hit on the label matters far more than one
    buried in a description, so the weights keep an incidental word in prose
    from outranking the setting the person actually meant.
    """
    best: int | None = None
    for text, weight in fields:
        value = score(query, text)
        if value is None:
            continue
        weighted = value * weight
        if best is None or weighted > best:
            best = weighted
    return best


def score_query(query: str, fields: list[tuple[str, int]]) -> int | None:
    """Score a whole query, which may be several words.

    Every word has to match something — "wake lan" should not return every
    setting mentioning wake — but they need not match the same field, so
    "boot usb" finds a setting whose label says boot and whose description
    mentions USB.
    """
    words = query.split()
    if not words:
        return 0
    total = 0
    for word in words:
        value = score_fields(word, fields)
        if value is None:
            return None
        total += value
    return total
