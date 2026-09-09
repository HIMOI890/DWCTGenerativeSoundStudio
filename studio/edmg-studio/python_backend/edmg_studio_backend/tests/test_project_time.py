from fractions import Fraction

import pytest

from edmg_studio_backend.domain.project_time import ProjectClock, frame_rate, int64, nearest


@pytest.mark.parametrize(
    "value,expected",
    [(Fraction(1, 2), 1), (Fraction(-1, 2), -1), (Fraction(3, 2), 2), (Fraction(-3, 2), -2)],
)
def test_rounding_ties_away_from_zero(value, expected):
    assert nearest(value) == expected


@pytest.mark.parametrize("fps", ["23.976", "24", "25", "29.97", "30", "50", "59.94", "60"])
def test_frame_sample_round_trip(fps):
    clock = ProjectClock(fps=frame_rate(fps))
    for frame in (-100000, -1, 0, 1, 1798, 1800, 17982, 107892, 999999):
        assert clock.samples_to_frame(clock.frame_to_samples(frame)) == frame
        assert clock.parse_timecode(clock.timecode(frame)) == frame


def test_drop_frame_boundaries_and_long_duration():
    clock = ProjectClock(fps=frame_rate("29.97"))
    for frame, label in [
        (1799, "00:00:59;29"),
        (1800, "00:01:00;02"),
        (17982, "00:10:00;00"),
        (107892, "01:00:00;00"),
        (2589408, "24:00:00;00"),
    ]:
        assert clock.timecode(frame, drop_frame=True) == label
        assert clock.parse_timecode(label) == frame
    with pytest.raises(ValueError):
        clock.parse_timecode("00:01:00;00")
    clock60 = ProjectClock(fps=frame_rate("59.94"))
    assert clock60.timecode(3600, drop_frame=True) == "00:01:00;04"
    assert clock60.parse_timecode("00:01:00;04") == 3600


@pytest.mark.parametrize("value", [True, 1.5, "1e3", "01", str(1 << 63), str(-(1 << 63) - 1)])
def test_invalid_int64_rejected(value):
    with pytest.raises(ValueError):
        int64(value)


def test_sample_precision_above_javascript_integer_limit():
    position = "9007199254740993"
    assert int64(position) == 9007199254740993
    assert ProjectClock().seconds(position) * 48000 == int(position)
    assert ProjectClock().samples("0.0000104166666666666666666666667") == 1
