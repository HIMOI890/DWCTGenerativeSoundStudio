from core.scheduler import DeforumScheduler
from config.config_system import AnimationConfig


def test_scheduler_aligns_lyrics():
    beats = [0.5, 1.0, 1.5]
    energy = [0.2, 0.6, 0.9]
    lyrics = [{"start": 0.9, "end": 1.2, "text": "hello"}]

    cfg = AnimationConfig(fps=30, duration=3, resolution="512x512")
    sched = DeforumScheduler(cfg)
    schedule = sched.build(beats, energy, lyrics)

    assert len(schedule) == 3
    assert any(step["prompt"] == "hello" for step in schedule)
