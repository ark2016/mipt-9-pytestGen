"""Branches that end the function body (exit arcs)."""


def log_negative(x: int, sink: list[int]) -> None:
    if x < 0:
        sink.append(x)


def log_either(x: int, sink: list[str]) -> None:
    sink.append("start")
    if x < 0:
        sink.append("neg")
    else:
        sink.append("non-neg")


async def tail_async(x: int, sink: list[int]) -> None:
    if x:
        sink.append(x)
