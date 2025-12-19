from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable, List, Sequence, TypeVar

T = TypeVar("T")

@dataclass
class RNG:
    """Deterministic RNG (xorshift32). Good enough as a controllable baseline."""
    state: int

    def _next_u32(self) -> int:
        x = self.state & 0xFFFFFFFF
        x ^= (x << 13) & 0xFFFFFFFF
        x ^= (x >> 17) & 0xFFFFFFFF
        x ^= (x << 5) & 0xFFFFFFFF
        self.state = x
        return x

    def random(self) -> float:
        # [0,1)
        return (self._next_u32() & 0xFFFFFFFF) / 2**32

    def randint(self, a: int, b: int) -> int:
        if a > b:
            a, b = b, a
        span = b - a + 1
        return a + (self._next_u32() % span)

    def choice(self, seq: Sequence[T]) -> T:
        if not seq:
            raise ValueError("choice() on empty sequence")
        return seq[self.randint(0, len(seq) - 1)]

    def shuffle(self, arr: List[T]) -> None:
        for i in range(len(arr) - 1, 0, -1):
            j = self.randint(0, i)
            arr[i], arr[j] = arr[j], arr[i]
