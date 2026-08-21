from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, TypeVar

T = TypeVar("T")

_M = 2147483647
_A = 48271


@dataclass
class RNG:
    """
    Deterministic RNG that matches Dwellings.js:
    - Random float: seed = (48271*seed) % 2147483647; return seed / 2147483647
    - Integers are derived via floor(rnd()*n), NOT modulo.
    - Shuffle matches com.watabou.utils.ArrayExtender.shuffle (insertion shuffle).
    """
    state: int

    def __post_init__(self) -> None:
        # Park-Miller requires state in [1, M-1]
        self.state = int(self.state) % _M
        if self.state <= 0:
            self.state += _M - 1

    def _next_seed(self) -> int:
        self.state = (_A * self.state) % _M
        return self.state

    def _next_u32(self) -> int:
        """
        Compatibility method for existing code.
        Note: JS doesn't expose this, but we keep it for your per-floor seeding.
        """
        return self._next_seed()

    def random(self) -> float:
        """JS t.float / ca.rnd"""
        return self._next_seed() / float(_M)

    def rand_int(self, n: int) -> int:
        """JS: (rnd() * n) | 0"""
        n = int(n)
        if n <= 0:
            raise ValueError("rand_int(n) requires n > 0")
        return int(self.random() * n)

    def randint(self, a: int, b: int) -> int:
        """Inclusive [a, b], built from floor(rnd()*span)."""
        a = int(a); b = int(b)
        if a > b:
            a, b = b, a
        span = b - a + 1
        return a + self.rand_int(span)

    def choice(self, seq: Sequence[T]) -> T:
        if not seq:
            raise ValueError("choice() on empty sequence")
        return seq[self.rand_int(len(seq))]

    def shuffle(self, a: list) -> None:
        """
        JS ca.shuffle: 逐个元素插入到新数组的随机位置（不是 Fisher-Yates）
        会影响 RNG 消耗顺序，必须一致
        """
        b = []
        for x in a:
            idx = self.rndi(len(b) + 1)  # [0, len(b)]
            b.insert(idx, x)
        a[:] = b

    # 下面这些方法在“复刻 JS”时很常用（下一步你会用到）
    def shuffled(self, seq: Sequence[T]) -> List[T]:
        out = list(seq)
        self.shuffle(out)
        return out

    def pick(self, a: list):
        """JS ca.pick: random element then remove it"""
        if not a:
            raise ValueError("pick from empty list")
        idx = self.rndi(len(a))
        return a.pop(idx)

    def subset(self, a: list, k: int) -> list:
        """JS ca.subset: shuffle(copy) then slice"""
        b = list(a)
        self.shuffle(b)
        return b[: max(0, min(k, len(b)))]

    def rndi(self, n: int) -> int:
        """JS: floor(rnd() * n), returns [0, n-1]"""
        if n <= 0:
            raise ValueError("n must be > 0")
        return int(self.random() * n)

    def weighted(self, items, weights):
        """JS ca.weighted(items, weights)"""
        if len(items) != len(weights):
            raise ValueError("items/weights length mismatch")
        total = sum(weights)
        r = self.random() * total
        acc = 0.0
        for it, w in zip(items, weights):
            acc += w
            if r <= acc:
                return it
        return items[0]  # JS fallback