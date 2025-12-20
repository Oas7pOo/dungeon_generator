from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable, List, Sequence, TypeVar

T = TypeVar("T")

@dataclass
class RNG:
    """Deterministic RNG (Park-Miller LCG). Matches Dwellings.js implementation."""
    state: int

    def _next_u32(self) -> int:
        # Park-Miller LCG: seed = (48271*seed) % 2147483647
        self.state = (48271 * self.state) % 2147483647
        return self.state & 0xFFFFFFFF

    def random(self) -> float:
        # [0,1)
        return (self._next_u32() & 0x7FFFFFFF) / 2147483647.0

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
