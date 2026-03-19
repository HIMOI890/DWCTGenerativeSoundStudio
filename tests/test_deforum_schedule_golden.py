from __future__ import annotations

from enhanced_deforum_music_generator.core.deforum_schedule_format import format_schedule


def test_format_schedule_golden():
    s = format_schedule([(0, 1.0), (12, 1.25), (48, 0.875), (96, 1.125)], precision=4)
    assert s == "0:(1.0000), 12:(1.2500), 48:(0.8750), 96:(1.1250)"
