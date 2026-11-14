"""Early returns, raises and asserts guarding later branches."""


def classify(age: int, role: str) -> str:
    if age < 0:
        raise ValueError("age must be >= 0")
    if age >= 18 and role == "admin":
        return "full"
    return "limited"


def sign(x: int) -> int:
    if x < 0:
        return -1
    if x == 0:
        return 0
    return 1


def inverted(x: int) -> int:
    if x > 0:
        pass
    else:
        return -1
    if x > 100:
        return 100
    return x


def nested_exit(a: int, b: int) -> int:
    if a > 0:
        if b > 0:
            return 1
        else:
            return 2
    if a < -10:
        return 3
    return 4
