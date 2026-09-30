"""
Page-number pagination for list views (see templates/_pagination.html).
"""
import math
from dataclasses import dataclass


@dataclass
class Page:
    number: int
    per_page: int
    total: int

    @classmethod
    def from_arg(cls, raw, per_page: int, total: int) -> "Page":
        """Build from a ?page= value, clamped to the available pages."""
        try:
            number = int(raw)
        except (TypeError, ValueError):
            number = 1
        pages = max(1, math.ceil(total / per_page))
        return cls(number=min(max(1, number), pages), per_page=per_page, total=total)

    @property
    def pages(self) -> int:
        return max(1, math.ceil(self.total / self.per_page))

    @property
    def offset(self) -> int:
        return (self.number - 1) * self.per_page

    @property
    def has_prev(self) -> bool:
        return self.number > 1

    @property
    def has_next(self) -> bool:
        return self.number < self.pages

    def window(self, around: int = 2) -> list:
        """Page numbers to link, with None for gaps, e.g. [1, None, 4, 5, 6, None, 12]."""
        shown = {1, self.pages} | set(
            range(max(1, self.number - around), min(self.pages, self.number + around) + 1)
        )
        out, previous = [], 0
        for n in sorted(shown):
            if n - previous > 1:
                out.append(None)
            out.append(n)
            previous = n
        return out
