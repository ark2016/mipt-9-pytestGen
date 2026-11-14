"""Small string utilities with branching rules."""


def slugify(text: str, max_length: int = 50) -> str:
    """Turn text into a lowercase ASCII slug separated by single dashes.

    Non-ASCII and non-alphanumeric characters become separators. The result
    is cut to ``max_length`` characters without a trailing dash.
    """
    if max_length < 1:
        raise ValueError("max_length must be positive")
    parts: list[str] = []
    for char in text.lower():
        if char.isascii() and char.isalnum():
            parts.append(char)
        elif parts and parts[-1] != "-":
            parts.append("-")
    slug = "".join(parts).strip("-")
    return slug[:max_length].rstrip("-")


def is_palindrome(text: str, *, ignore_case: bool = True, ignore_spaces: bool = True) -> bool:
    """Return True if the text reads the same backwards.

    Options control whether letter case and whitespace are taken into account.
    """
    prepared = text.lower() if ignore_case else text
    if ignore_spaces:
        prepared = "".join(prepared.split())
    return prepared == prepared[::-1]


def truncate(text: str, limit: int, suffix: str = "...") -> str:
    """Shorten text to at most ``limit`` characters, ending it with ``suffix``.

    Text that already fits is returned unchanged. If the suffix alone does not
    fit into ``limit``, the plain prefix of ``limit`` characters is returned.
    """
    if limit < 0:
        raise ValueError("limit must be non-negative")
    if len(text) <= limit:
        return text
    if len(suffix) >= limit:
        return text[:limit]
    return text[: limit - len(suffix)].rstrip() + suffix


def classify_password(pw: str) -> str:
    """Classify a password as invalid, too short, weak, medium or strong.

    Passwords must not contain whitespace and must be at least 8 characters.
    Strength depends on the number of character classes (lower, upper,
    digit, other) and on the length.
    """
    if any(ch.isspace() for ch in pw):
        return "invalid"
    if len(pw) < 8:
        return "too short"
    classes = _count_char_classes(pw)
    if classes == 1:
        return "weak"
    if classes == 2 or len(pw) < 12:
        return "medium"
    return "strong"


def _count_char_classes(text: str) -> int:
    """Count how many of the four character classes appear in the text."""
    checks = (
        any(ch.islower() for ch in text),
        any(ch.isupper() for ch in text),
        any(ch.isdigit() for ch in text),
        any(not ch.isalnum() for ch in text),
    )
    return sum(checks)
