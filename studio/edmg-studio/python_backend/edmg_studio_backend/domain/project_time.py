"""Exact project time. JSON sample positions are decimal int64 strings."""

from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction

INT64_MIN = -(1 << 63)
INT64_MAX = (1 << 63) - 1
FRAME_RATES = {
    "23.976": Fraction(24000, 1001),
    "24": Fraction(24),
    "25": Fraction(25),
    "29.97": Fraction(30000, 1001),
    "30": Fraction(30),
    "50": Fraction(50),
    "59.94": Fraction(60000, 1001),
    "60": Fraction(60),
}


def int64(value: str | int) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("Sample positions must be decimal int64 strings or integers")
    if isinstance(value, str) and not re.fullmatch(r"-?(0|[1-9][0-9]*)", value):
        raise ValueError("Invalid decimal sample position")
    result = int(value)
    if not INT64_MIN <= result <= INT64_MAX:
        raise ValueError("Sample position exceeds int64 range")
    return result


def nearest(value: Fraction) -> int:
    """Round to nearest integer, with ties away from zero."""
    sign = -1 if value < 0 else 1
    numerator = abs(value.numerator)
    return sign * ((2 * numerator + value.denominator) // (2 * value.denominator))


def frame_rate(value: object) -> Fraction:
    if isinstance(value, dict):
        n, d = value.get("numerator"), value.get("denominator")
        if type(n) is not int or type(d) is not int or d <= 0:
            raise ValueError("Frame rate requires integer numerator and positive denominator")
        rate = Fraction(n, d)
    else:
        text = str(value)
        rate = FRAME_RATES[text] if text in FRAME_RATES else Fraction(text)
    # Older Studio projects include preview/export rates such as 12 and 15 fps.
    # Preserve those exact rates; FRAME_RATES defines the professional presets.
    if not 0 < rate <= 240:
        raise ValueError("Project frame rate must be greater than zero and at most 240")
    return rate


@dataclass(frozen=True)
class ProjectClock:
    sample_rate: int = 48000
    fps: Fraction = Fraction(30)

    def __post_init__(self):
        if type(self.sample_rate) is not int or not 8000 <= self.sample_rate <= 384000:
            raise ValueError("Sample rate must be between 8000 and 384000 Hz")
        if not 0 < self.fps <= 240:
            raise ValueError("Invalid project frame rate")

    @classmethod
    def from_timeline(cls, timeline: dict) -> ProjectClock:
        settings = timeline.get("timebase") or {}
        return cls(
            settings.get("sample_rate", 48000),
            frame_rate(settings.get("frame_rate", timeline.get("fps", 30))),
        )

    def to_dict(self) -> dict:
        return {
            "sample_rate": self.sample_rate,
            "frame_rate": {"numerator": self.fps.numerator, "denominator": self.fps.denominator},
        }

    def samples(self, seconds: object) -> int:
        if isinstance(seconds, bool):
            raise ValueError("Time must be a finite number")
        return int64(nearest(Fraction(str(seconds)) * self.sample_rate))

    def seconds(self, samples: str | int) -> Fraction:
        return Fraction(int64(samples), self.sample_rate)

    def frame_to_samples(self, frame: int) -> int:
        return int64(nearest(Fraction(int64(frame) * self.sample_rate, 1) / self.fps))

    def samples_to_frame(self, samples: str | int) -> int:
        return nearest(self.seconds(samples) * self.fps)

    def snap_frame(self, samples: str | int) -> int:
        return self.frame_to_samples(self.samples_to_frame(samples))

    def timecode(self, frame: int, *, drop_frame: bool = False) -> str:
        frame = int64(frame)
        prefix, count = ("-" if frame < 0 else ""), abs(frame)
        nominal = nearest(self.fps)
        if drop_frame:
            drop = self._drop_count()
            per_minute = nominal * 60 - drop
            per_ten = nominal * 600 - drop * 9
            tens, remainder = divmod(count, per_ten)
            count += tens * drop * 9 + drop * max(0, (remainder - drop) // per_minute)
        seconds, frames = divmod(count, nominal)
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        return (
            f"{prefix}{hours:02}:{minutes:02}:{seconds:02}{';' if drop_frame else ':'}{frames:02}"
        )

    def parse_timecode(self, text: str) -> int:
        match = re.fullmatch(r"(-?)([0-9]{2,}):([0-5][0-9]):([0-5][0-9])([:;])([0-9]{2})", text)
        if not match:
            raise ValueError("Expected HH:MM:SS:FF or HH:MM:SS;FF")
        sign, hours, minutes, seconds, separator, frames = match.groups()
        h, m, s, f = map(int, (hours, minutes, seconds, frames))
        nominal = nearest(self.fps)
        if f >= nominal:
            raise ValueError("Frame number exceeds nominal frame rate")
        total_minutes = h * 60 + m
        result = (total_minutes * 60 + s) * nominal + f
        if separator == ";":
            drop = self._drop_count()
            if m % 10 and s == 0 and f < drop:
                raise ValueError("This timecode label is omitted by drop-frame numbering")
            result -= drop * (total_minutes - total_minutes // 10)
        return int64(-result if sign else result)

    def _drop_count(self) -> int:
        if self.fps not in (Fraction(30000, 1001), Fraction(60000, 1001)):
            raise ValueError("Drop-frame requires 30000/1001 or 60000/1001 fps")
        return 2 if self.fps == Fraction(30000, 1001) else 4
