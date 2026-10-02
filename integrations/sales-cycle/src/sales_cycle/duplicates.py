"""Is this feature request one the team has already been shown?

The copilot words the same ask differently each time it hears it ("CSV Export of Results", then "Export results to CSV"), so an exact-title match
misses it and a second card — and, if approved, a second agent run building the same thing — follows. This compares requests by their
words: the title and body reduced to their content words (filler like "we should add a" dropped, plurals and endings trimmed) and scored by
overlap. It does no I/O, so the rule is tested directly.

It leans toward NOT merging: a missed repeat only costs one more card, but merging two different requests would hide one. So the bar is
high (Jaccard, not the looser overlap measure) and at least two content words must be shared — "export to PDF" and "export to CSV" stay apart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

THRESHOLD = 0.7
MIN_SHARED_WORDS = 2

# Words that carry no part of WHAT is being asked for: conversation, politeness, and "add a button" scaffolding.
_STOP = frozenset("""
a an the and or but if of to for in on at by with from into onto over under as is are was were be been being it its this that these those
i we you they he she me us our your their my them there here
should would could can will shall may might must do does did done have has had having want wants wanted wish need needs needed like likes
please maybe also just really very so then than too let lets get gets got make makes made
suggest suggests suggested think thought say says said ask asks asked request requests requested feature features ability able option
add adds added adding new support
""".split())


def _stem(word: str) -> str:
    if len(word) > 5 and word.endswith("ing"):
        word = word[:-3]
    elif len(word) > 4 and word.endswith("ed"):
        word = word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    return word


def words(text: str) -> frozenset[str]:
    return frozenset(
        _stem(w) for w in re.findall(r"[a-z0-9]+", text.casefold())
        if len(w) > 1 and w not in _STOP
    )


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    shared = len(a & b)
    return shared / len(a | b) if shared >= MIN_SHARED_WORDS else 0.0


def similarity(title_a: str, body_a: str, title_b: str, body_b: str) -> float:
    """0..1: the better of how alike the titles are and how alike the whole requests are."""
    ta, tb = words(title_a), words(title_b)
    return max(_jaccard(ta, tb), _jaccard(ta | words(body_a), tb | words(body_b)))


@dataclass(frozen=True)
class Known:
    """A request already on the table, as far as matching is concerned."""
    id: int
    title: str
    body: str


def find_duplicate(title: str, body: str, known: list[Known], *, threshold: float = THRESHOLD) -> Known | None:
    """The most similar known request at or above the threshold, else None. The earliest wins a tie (it is the original)."""
    best, best_score = None, 0.0
    for k in sorted(known, key=lambda k: k.id):
        score = similarity(title, body, k.title, k.body)
        if score >= threshold and score > best_score:
            best, best_score = k, score
    return best
