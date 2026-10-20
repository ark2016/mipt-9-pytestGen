"""Ternaries, asserts, try/finally, with, match."""

import contextlib


def ternary(x: int) -> str:
    return "pos" if x > 0 else "non-pos"


def nested_ternary(x: int) -> str:
    return "a" if x > 0 else ("b" if x < -5 else "c")


def checked(x: int) -> int:
    assert x >= 0, "negative"
    return x


def guarded(path: str | None) -> int:
    try:
        if path:
            value = len(path)
        else:
            value = 0
            value += 1
    except TypeError:
        value = -1
    finally:
        value = max(value, 0)
    return value


def parse(text: str) -> int | None:
    try:
        number = int(text)
    except ValueError:
        return None
    else:
        if number > 9:
            return 9
    return number


def with_if(items: list[int]) -> int:
    with contextlib.nullcontext():
        if items:
            return 1
    return 0


def matcher(cmd: str) -> int:
    match cmd:
        case "go":
            return 1
        case _:
            if cmd == "stop":
                return 2
    return 0
