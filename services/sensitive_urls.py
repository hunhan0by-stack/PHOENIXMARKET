"""
Addresses that carry a secret: order tracking and password-reset links.

Such an address is a key. Whoever has it sees the order (delivered account
details included) or can set a new password. It is never stored in the visit
statistics or the log as it is, and its page is sent with headers that keep it
out of caches, search engines and the Referer sent to other sites.

A route counts as secret-bearing when one of its URL variables is named like a
secret (``token``, ``reset_token``, ``secret`` ...). New routes that follow this
naming are covered without further changes. tests/test_sensitive_urls.py fails
for a route variable of any other name until it is classified, so a secret
under a new name cannot slip through unnoticed.
"""
import re
from functools import lru_cache

from flask import current_app

SECRET_WORDS = frozenset({"token", "secret", "password", "signature", "otp", "nonce"})

# <name> or <converter:name> in a route such as /track/<token>
_VARIABLE = re.compile(r"<(?:[^<>:]+:)?([^<>:]+)>")


def is_secret_name(name: str) -> bool:
    """True for URL variable names such as token, reset_token, secret."""
    return any(word in SECRET_WORDS for word in name.lower().split("_"))


@lru_cache(maxsize=None)
def _compile(rule: str):
    """(regex, secret names) matching the rule up to its last secret variable, or None.

    Matching only that far also covers longer addresses under it
    (/track/<token>/cancel, a mistyped /track/<token>/x).
    """
    parts = _VARIABLE.split(rule)  # literal, variable name, literal, ...
    secret_at = [i for i in range(1, len(parts), 2) if is_secret_name(parts[i])]
    if not secret_at:
        return None
    pattern, names = "", []
    for i, part in enumerate(parts[: secret_at[-1] + 1]):
        if i % 2 == 0:
            pattern += re.escape(part)
        elif is_secret_name(part):
            pattern += "([^/]+)"
            names.append(part)
        else:
            pattern += "[^/]+"
    return re.compile(pattern + "(?=/|$)"), tuple(names)


def mask(path: str) -> str:
    """The path with each secret part replaced by its name: /track/abc… → /track/<token>."""
    for rule in current_app.url_map.iter_rules():
        compiled = _compile(rule.rule)
        if not compiled:
            continue
        pattern, names = compiled
        match = pattern.match(path)
        if match:
            for group in range(len(names), 0, -1):
                start, end = match.span(group)
                path = f"{path[:start]}<{names[group - 1]}>{path[end:]}"
            return path
    return path


def is_sensitive(path: str) -> bool:
    return mask(path) != path
