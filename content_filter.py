"""
content_filter.py
──────────────────
Simple profanity / inappropriate content filter for TTS input.
"""

import re


BLOCKLIST = [
    "gay", "slut", "milf", "sybau",
    "fuck", "fucking", "fucker", "fck", "fuk", "fk",
    "shit", "shitty", "bullshit", "sht",
    "bitch", "bitches", "btch",
    "asshole", "assholes", "ashole",
    "dick", "dicks", "cock", "cocks",
    "pussy", "pussies",
    "cunt", "cunts", "cnt",
    "bastard", "bastards",
    "piss", "pissed",
    "wanker", "tosser",
    "sex", "porn", "naked", "nude", "horny", "boobs", "tits",
    "rape", "rapist",
    "blowjob", "handjob",
    "nigger", "nigga", "niggas",
    "chink", "spic", "kike", "wetback",
    "faggot", "faggots", "fag",
    "tranny", "trannies",
    "retard", "retarded",
    "kill", "murder", "die", "death", "suicide",
    "shoot", "stab", "bomb",
    "cocaine", "heroin", "meth", "crack",
    "marijuana", "weed",
]


_PATTERNS = [re.compile(r"\b" + re.escape(w) + r"\b", re.IGNORECASE) for w in BLOCKLIST]


def _normalize(text):
    text = text.lower()
    subs = {
        "@": "a", "4": "a",
        "0": "o",
        "1": "i", "!": "i",
        "3": "e",
        "$": "s", "5": "s",
        "7": "t",
        "*": "", ".": "", "-": "", "_": "",
    }
    for old, new in subs.items():
        text = text.replace(old, new)
    return text


def find_violation(text):
    if not text:
        return None
    for word, pattern in zip(BLOCKLIST, _PATTERNS):
        if pattern.search(text):
            return word
    normalized = _normalize(text)
    if normalized != text.lower():
        for word, pattern in zip(BLOCKLIST, _PATTERNS):
            if pattern.search(normalized):
                return word
    return None


def is_clean(text):
    return find_violation(text) is None
