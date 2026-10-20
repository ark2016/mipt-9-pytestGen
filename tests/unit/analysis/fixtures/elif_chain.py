"""``elif`` chains with and without a final ``else``."""


def grade(score: int) -> str:
    if score >= 90:
        result = "A"
    elif score >= 80:
        result = "B"
    elif score >= 70:
        result = "C"
    else:
        result = "F"
    return result


def bucket(n: int, out: list[str]) -> None:
    if n < 0:
        out.append("neg")
    elif n == 0:
        out.append("zero")
    elif n < 10:
        out.append("small")


def early_chain(n: int) -> str:
    if n < 0:
        return "neg"
    elif n == 0:
        return "zero"
    if n > 100:
        return "huge"
    return "pos"
