"""Loops: nested ``if`` in ``for``, ``while``, ``else`` clauses and exit arcs."""


def count_even(items: list[int]) -> int:
    total = 0
    for item in items:
        if item % 2 == 0:
            total += 1
    return total


def first_negative(values: list[int]) -> int | None:
    for value in values:
        if value < 0:
            break
    else:
        return None
    return value


def drain(n: int) -> int:
    steps = 0
    while n > 0:
        if steps > 50:
            break
        n -= 1
        steps += 1
    else:
        steps += 100
    return steps


def spin(n: int) -> int:
    while True:
        if n > 3:
            return n
        n += 1


def sum_positive(rows: list[list[int]]) -> int:
    total = 0
    for row in rows:
        for cell in row:
            if cell > 0:
                total += cell
    return total


def collect(items: list[int], out: list[int]) -> None:
    for item in items:
        out.append(item)


def indirect(x: list[int]) -> int:
    items = sorted(x)
    count = 0
    for item in items:
        count += item
    return count


def computed(n: int) -> int:
    total = 0
    for i in range(n):
        total += i
    return total
