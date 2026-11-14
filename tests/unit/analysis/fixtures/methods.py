"""Methods of a top-level class."""


class Account:
    """A bank account."""

    def __init__(self, balance: int = 0, owner: str = "anon") -> None:
        self.balance = balance
        self.owner = owner

    def withdraw(self, amount: int) -> int:
        if amount > self.balance:
            raise ValueError("insufficient funds")
        self.balance -= amount
        return self.balance

    @staticmethod
    def fee(amount: int) -> int:
        if amount > 100:
            return 5
        return 1

    @classmethod
    def empty(cls) -> "Account":
        return cls()

    @property
    def rich(self) -> bool:
        return self.balance > 1000

    def tag(self, sink: list[str], loud: bool) -> None:
        if loud:
            sink.append(self.owner)
