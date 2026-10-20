"""Straight-line assignments substituted into path conditions."""


def double_check(x: int) -> str:
    y = x * 2
    if y > 10:
        return "big"
    return "small"


def transitive(a: int, b: int) -> str:
    s = a + b
    t = s * 2
    if t > 10:
        return "big"
    return "small"


def reassigned(x: int) -> str:
    y = x + 1
    y = y * 2
    if y > 3:
        return "big"
    return "small"


def loop_assigned(items: list[int]) -> int:
    total = 0
    for item in items:
        last = item * 2
        if last > 3:
            total += 1
    return total


def after_guard(x: int) -> str:
    if x < 0:
        return "neg"
    y = x + 1
    if y > 5:
        return "big"
    return "small"


def mutable(x: int) -> str:
    seen = []
    seen.append(x)
    if seen:
        return "some"
    return "none"
