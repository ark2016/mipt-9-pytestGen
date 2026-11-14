# ruff: noqa: PLW0603, F842
"""Statements without bytecode (``global``, bare annotations) as arc destinations."""

G = 0


def global_after_if(x: int, sink: list[int]) -> list[int]:
    if x:
        sink.append(1)
    global G
    G = 1
    return sink


def annotation_after_if(x: int, sink: list[int]) -> list[int]:
    if x:
        sink.append(1)
    y: int
    sink.append(2)
    return sink


def global_first_in_body(x: int) -> int:
    if x:
        global G
        G = 2
    return x
