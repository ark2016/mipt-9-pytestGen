"""Decorated functions: the code object starts at the first decorator."""

import functools


def traced(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)

    return wrapper


@traced
def pick(flag: bool, left: int, right: int) -> int:
    if flag:
        return left
    return right


@traced
@traced
def log_big(x: int, sink: list[int]) -> None:
    if x > 10:
        sink.append(x)
