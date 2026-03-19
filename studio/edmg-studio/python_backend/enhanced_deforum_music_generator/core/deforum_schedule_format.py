from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple


def format_schedule(pairs: Sequence[Tuple[int, float]], *, precision: int = 4) -> str:
    """Format Deforum schedule like: `0:(1.0000), 12:(1.2500)`.

    Deforum typically accepts a comma-separated list of keyframe:value pairs.
    """
    if not pairs:
        return ""
    fmt = f"{{:.{precision}f}}"
    return ", ".join(f"{int(k)}:({fmt.format(float(v))})" for k, v in pairs)


@dataclass(frozen=True)
class KeyframedSchedule:
    name: str
    pairs: List[Tuple[int, float]]

    def to_deforum(self, *, precision: int = 4) -> str:
        return format_schedule(self.pairs, precision=precision)
