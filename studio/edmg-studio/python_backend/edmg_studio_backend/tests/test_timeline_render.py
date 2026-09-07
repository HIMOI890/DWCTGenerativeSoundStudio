from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from edmg_studio_backend.tests.revision_client import TestClient
from pydantic import ValidationError

from edmg_studio_backend import app as backend_app
from edmg_studio_backend.schemas import TimelineRenderRequest
from edmg_studio_backend.services import ffmpeg as ffmpeg_service
from edmg_studio_backend.store.jobs import JobStore
from edmg_studio_backend.store.projects import ProjectStore


@pytest.mark.parametrize(
    ("quality", "expected"),
    [("high", 18), ("medium", 23), ("low", 28), ("31", 31), (12, 12)],
)
def test_timeline_render_request_normalizes_quality(quality: str | int, expected: int) -> None:
    request = TimelineRenderRequest(quality=quality, fps=23.976)

    assert request.quality == expected
    assert request.fps == pytest.approx(23.976)


@pytest.mark.parametrize("quality", ["unknown", "0", "52"])
def test_timeline_render_request_rejects_invalid_quality(quality: str) -> None:
    with pytest.raises(ValidationError):
        TimelineRenderRequest(quality=quality)


def test_timeline_render_request_rejects_invalid_codec_pair() -> None:
    with pytest.raises(ValidationError):
        TimelineRenderRequest(video_codec="prores", audio_codec="aac")


@pytest.mark.parametrize(
    "settings",
    [
        {"width": 255},
        {"width": 7690},
        {"height": 257},
        {"fps": 0},
        {"fps": 121},
    ],
)
def test_timeline_render_request_rejects_invalid_dimensions_and_fps(
    settings: dict[str, int],
) -> None:
    with pytest.raises(ValidationError):
        TimelineRenderRequest(**settings)


def test_timeline_render_request_sanitizes_bounded_name_and_accepts_codec_pairs() -> None:
    request = TimelineRenderRequest(
        video_codec="prores",
        audio_codec="pcm_s16le",
        name="  Final: Cut?  " + ("x" * 100),
    )

    assert request.name == ("Final_ Cut_ " + ("x" * 68))
    assert len(request.name) == 80
    assert TimelineRenderRequest(video_codec="hevc", audio_codec="aac").video_codec == "hevc"


def _video_file(project_dir: Path, name: str = "clip.mp4") -> Path:
    source = project_dir / "outputs" / "videos" / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"not-real-video")
    return source


def _image_file(project_dir: Path, name: str = "overlay.png") -> Path:
    source = project_dir / "outputs" / "images" / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"not-real-image")
    return source


def test_outputs_include_nested_layered_animation_for_timeline_media_library(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ProjectStore(tmp_path / "data")
    project = store.create("Nested Layered Output")
    nested_video = (
        store.project_dir(project.id)
        / "outputs"
        / "videos"
        / "layered_job-123"
        / "parallax_animation.mp4"
    )
    nested_video.parent.mkdir(parents=True, exist_ok=True)
    nested_video.write_bytes(b"real-route-placeholder")
    monkeypatch.setattr(backend_app, "store", store)

    response = backend_app.list_outputs(project.id)

    assert any(
        str(item["path"]).replace("\\", "/")
        == "outputs/videos/layered_job-123/parallax_animation.mp4"
        for item in response["videos"]
    )


@pytest.mark.parametrize(
    "source",
    [
        "../clip.mp4",
        r"C:\media\clip.mp4",
        "/media/clip.mp4",
        "missing.mp4",
        "outputs/videos",
    ],
)
def test_timeline_source_must_be_confined_file(tmp_path: Path, source: str) -> None:
    project_dir = tmp_path / "project"
    (project_dir / "outputs" / "videos").mkdir(parents=True)

    with pytest.raises(ValueError):
        ffmpeg_service._resolve_timeline_source(project_dir, source)


def test_prepare_timeline_plan_normalizes_source_and_rejects_no_video(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_dir = tmp_path / "project"
    source = _video_file(project_dir)
    monkeypatch.setattr(ffmpeg_service, "has_video_stream", lambda *_args: True)
    monkeypatch.setattr(ffmpeg_service, "has_audio_stream", lambda *_args: True)

    plan = ffmpeg_service.prepare_timeline_render_plan(
        ffmpeg_path="ffmpeg",
        project_dir=project_dir,
        timeline={
            "tracks": [
                {
                    "id": "v1",
                    "clips": [
                        {
                            "data": {"source_path": source.relative_to(project_dir).as_posix()},
                            "start_s": 1,
                            "end_s": 3,
                        }
                    ],
                }
            ]
        },
    )

    assert plan["tracks"][0]["clips"][0]["source_path"] == "outputs/videos/clip.mp4"
    assert plan["duration_s"] == 3

    monkeypatch.setattr(ffmpeg_service, "has_video_stream", lambda *_args: False)
    with pytest.raises(ValueError, match="no video stream"):
        ffmpeg_service.prepare_timeline_render_plan(
            ffmpeg_path="ffmpeg",
            project_dir=project_dir,
            timeline={
                "tracks": [
                    {
                        "clips": [
                            {"source_path": "outputs/videos/clip.mp4", "start_s": 0, "end_s": 1}
                        ]
                    }
                ]
            },
        )


def test_prepare_timeline_plan_rejects_same_track_overlap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_dir = tmp_path / "project"
    _video_file(project_dir, "one.mp4")
    _video_file(project_dir, "two.mp4")
    monkeypatch.setattr(ffmpeg_service, "has_video_stream", lambda *_args: True)
    monkeypatch.setattr(ffmpeg_service, "has_audio_stream", lambda *_args: True)

    with pytest.raises(ValueError, match="Overlapping clips"):
        ffmpeg_service.prepare_timeline_render_plan(
            ffmpeg_path="ffmpeg",
            project_dir=project_dir,
            timeline={
                "tracks": [
                    {
                        "clips": [
                            {"source_path": "outputs/videos/one.mp4", "start_s": 0, "end_s": 2},
                            {"source_path": "outputs/videos/two.mp4", "start_s": 1, "end_s": 3},
                        ]
                    }
                ]
            },
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("fit_mode", "zoom", "fit mode"),
        ("opacity", 1.5, "opacity"),
        ("brightness", -2, "brightness"),
        ("contrast", 3, "contrast"),
        ("saturation", 4, "saturation"),
        ("rotation_deg", 45, "rotation"),
    ],
)
def test_prepare_timeline_plan_rejects_invalid_video_adjustment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
    message: str,
) -> None:
    project_dir = tmp_path / "project"
    _video_file(project_dir)
    monkeypatch.setattr(ffmpeg_service, "has_video_stream", lambda *_args: True)
    monkeypatch.setattr(ffmpeg_service, "has_audio_stream", lambda *_args: True)

    clip = {
        "source_path": "outputs/videos/clip.mp4",
        "start_s": 0,
        "end_s": 2,
        field: value,
    }
    with pytest.raises(ValueError, match=message):
        ffmpeg_service.prepare_timeline_render_plan(
            ffmpeg_path="ffmpeg",
            project_dir=project_dir,
            timeline={"tracks": [{"clips": [clip]}]},
        )


@pytest.mark.parametrize(
    ("video_codec", "audio_codec", "suffix", "video_encoder", "pixel_format"),
    [
        ("h264", "aac", ".mp4", "libx264", "yuv420p"),
        ("hevc", "aac", ".mp4", "libx265", "yuv420p"),
        ("prores", "pcm_s16le", ".mov", "prores_ks", "yuv422p10le"),
    ],
)
def test_timeline_command_builds_composition_and_codec_mapping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    video_codec: str,
    audio_codec: str,
    suffix: str,
    video_encoder: str,
    pixel_format: str,
) -> None:
    project_dir = tmp_path / "project"
    _video_file(project_dir, "one.mp4")
    _video_file(project_dir, "two.mp4")
    monkeypatch.setattr(ffmpeg_service, "has_video_stream", lambda *_args: True)
    monkeypatch.setattr(ffmpeg_service, "has_audio_stream", lambda *_args: True)
    monkeypatch.setattr(ffmpeg_service, "ensure_ffmpeg", lambda value: value)

    command, duration = ffmpeg_service.build_timeline_render_command(
        ffmpeg_path="ffmpeg",
        project_dir=project_dir,
        timeline={
            "tracks": [
                {
                    "id": "background",
                    "clips": [
                        {
                            "source_path": "outputs/videos/one.mp4",
                            "start_s": 1,
                            "end_s": 5,
                            "source_in_s": 2,
                            "speed": 4,
                            "volume": 0.5,
                            "fade_in_s": 0.25,
                            "fade_out_s": 0.5,
                            "fit_mode": "cover",
                            "opacity": 0.75,
                            "brightness": 0.1,
                            "contrast": 1.2,
                            "saturation": 0.8,
                            "rotation_deg": 90,
                            "flip_horizontal": True,
                        }
                    ],
                },
                {
                    "id": "overlay",
                    "clips": [
                        {
                            "source_path": "outputs/videos/two.mp4",
                            "start_s": 2,
                            "end_s": 3,
                            "muted": True,
                        }
                    ],
                },
            ]
        },
        output_path=tmp_path / f"master{suffix}",
        width=1920,
        height=1080,
        fps=23.976,
        video_codec=video_codec,
        audio_codec=audio_codec,
        quality=20,
    )

    graph = command[command.index("-filter_complex") + 1]
    assert duration == 5
    assert command[0] == "ffmpeg"
    assert any("color=c=black" in argument for argument in command)
    assert any("anullsrc" in argument for argument in command)
    assert "trim=start=2.000000" in graph
    assert "setpts=(PTS-STARTPTS)/4" in graph
    assert "scale=1920:1080" in graph
    assert "pad=1920:1080" in graph
    assert "fps=23.976" in graph
    assert "fade=t=in" in graph and "fade=t=out" in graph
    assert "transpose=clock" in graph
    assert "hflip" in graph
    assert "force_original_aspect_ratio=increase" in graph
    assert "crop=1920:1080" in graph
    assert "eq=brightness=0.1:contrast=1.2:saturation=0.8" in graph
    assert "format=rgba,colorchannelmixer=aa=0.75" in graph
    assert "atempo=2,atempo=2" in graph
    assert "volume=0.5" in graph
    assert "adelay=1000:all=1" in graph
    assert "overlay=eof_action=pass" in graph
    assert "amix=inputs=3" in graph
    assert command[command.index("-c:v") + 1] == video_encoder
    assert command[command.index("-pix_fmt") + 1] == pixel_format
    assert command[command.index("-c:a") + 1] == audio_codec
    assert command[-1].endswith(suffix)


def test_timeline_command_includes_winui_layers_and_loops_image_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "project"
    _video_file(project_dir)
    image = _image_file(project_dir)
    monkeypatch.setattr(ffmpeg_service, "has_video_stream", lambda *_args: True)
    monkeypatch.setattr(ffmpeg_service, "has_audio_stream", lambda *_args: True)
    monkeypatch.setattr(ffmpeg_service, "ensure_ffmpeg", lambda value: value)

    timeline = {
        "tracks": [
            {
                "id": "main",
                "clips": [
                    {
                        "source_path": "outputs/videos/clip.mp4",
                        "start_s": 0,
                        "end_s": 2,
                    }
                ],
            }
        ],
        "layers": [
            {
                "id": "artwork",
                "name": "Artwork overlay",
                "type": "image",
                "start_s": 0.5,
                "end_s": 1.5,
                "data": {
                    "source_path": "outputs/images/overlay.png",
                    "fit_mode": "contain",
                    "opacity": 0.6,
                    "fade_in_s": 0.25,
                    "fade_out_s": 0.25,
                },
            }
        ],
    }
    command, duration = ffmpeg_service.build_timeline_render_command(
        ffmpeg_path="ffmpeg",
        project_dir=project_dir,
        timeline=timeline,
        output_path=tmp_path / "master.mp4",
        width=1280,
        height=720,
        fps=24,
        video_codec="h264",
        audio_codec="aac",
        quality=20,
    )

    graph = command[command.index("-filter_complex") + 1]
    image_input = command.index(str(image))
    prepared = ffmpeg_service.prepare_timeline_render_plan(
        ffmpeg_path="ffmpeg",
        project_dir=project_dir,
        timeline=timeline,
    )
    round_tripped = ffmpeg_service.prepare_timeline_render_plan(
        ffmpeg_path="ffmpeg",
        project_dir=project_dir,
        timeline=prepared,
    )

    assert duration == 2
    assert prepared["tracks"][1]["is_layer"] is True
    assert prepared["tracks"][1]["clips"][0]["is_layer"] is True
    assert round_tripped["tracks"][1]["is_layer"] is True
    assert round_tripped["tracks"][1]["clips"][0]["is_layer"] is True
    assert command[image_input - 5 : image_input + 1] == [
        "-loop",
        "1",
        "-framerate",
        "24",
        "-i",
        str(image),
    ]
    assert "[1:v:0]" in graph
    assert "force_original_aspect_ratio=decrease" in graph
    assert "pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=black@0" in graph
    assert (
        "format=rgba,pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=black@0,colorchannelmixer=aa=0.6"
    ) in graph
    assert "fade=t=in:st=0:d=0.250000:alpha=1" in graph
    assert "fade=t=out:st=0.750000:d=0.250000:alpha=1" in graph
    assert "amix=inputs=2" in graph


def test_timeline_render_cancellation_terminates_and_removes_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "partial.mp4"
    output.write_bytes(b"partial")

    class FakeProcess:
        returncode: int | None = None
        terminated = False
        killed = False

        def poll(self) -> int | None:
            return self.returncode

        def terminate(self) -> None:
            self.terminated = True
            self.returncode = -15

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

        def wait(self, timeout: float | None = None) -> int:
            assert timeout == 5
            return int(self.returncode or 0)

        def communicate(self) -> tuple[str, str]:
            return "", ""

    process = FakeProcess()
    monkeypatch.setattr(ffmpeg_service.subprocess, "Popen", lambda *_args, **_kwargs: process)

    with pytest.raises(ffmpeg_service.TimelineRenderCanceled):
        ffmpeg_service.render_timeline_edited_master(
            command=["ffmpeg"],
            output_path=output,
            duration_s=10,
            is_canceled=lambda: True,
        )

    assert process.terminated
    assert not process.killed
    assert not output.exists()


def test_timeline_render_route_enqueues_normalized_persistent_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ProjectStore(tmp_path / "projects")
    jobs = JobStore(tmp_path / "jobs.sqlite3")
    project = store.create("Timeline")
    project.meta["timeline"] = {
        "tracks": [
            {
                "clips": [
                    {
                        "data": {"source_path": r"outputs\videos\clip.mp4"},
                        "start_s": 0,
                        "end_s": 2,
                    }
                ]
            }
        ]
    }
    store.save(project)
    normalized = {
        "duration_s": 2.0,
        "tracks": [
            {
                "id": "track-0",
                "clips": [
                    {
                        "id": "clip-0-0",
                        "source_path": "outputs/videos/clip.mp4",
                        "start_s": 0.0,
                        "end_s": 2.0,
                    }
                ],
            }
        ],
    }
    monkeypatch.setattr(backend_app, "store", store)
    monkeypatch.setattr(backend_app, "jobs", jobs)
    monkeypatch.setattr(
        backend_app,
        "prepare_timeline_render_plan",
        lambda **_kwargs: normalized,
    )
    monkeypatch.setattr(backend_app.worker, "start", lambda: None)
    monkeypatch.setattr(backend_app.worker, "stop", lambda: None)
    monkeypatch.setattr(
        backend_app,
        "settings",
        SimpleNamespace(**{**backend_app.settings.__dict__, "worker_autostart": True}),
    )

    with TestClient(backend_app.app) as client:
        response = client.post(
            f"/v1/projects/{project.id}/timeline/render",
            json={"quality": "high", "fps": 23.976},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["job"]["status"] == "queued"
    persisted = jobs.get(project.id, body["job"]["id"])
    assert persisted is not None
    assert persisted.type == "timeline_render"
    assert persisted.payload["timeline"] == normalized
    assert persisted.payload["settings"]["quality"] == 18
    assert persisted.payload["settings"]["fps"] == pytest.approx(23.976)


def test_timeline_render_worker_writes_artifact_and_project_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ProjectStore(tmp_path / "data")
    jobs = JobStore(store.projects_dir)
    project = store.create("Timeline worker")
    payload = {
        "settings": {
            "name": "final cut",
            "width": 1280,
            "height": 720,
            "fps": 24,
            "video_codec": "h264",
            "audio_codec": "aac",
            "quality": 20,
        },
        "timeline": {"tracks": [{"clips": [{"source_path": "outputs/videos/clip.mp4"}]}]},
    }
    job = jobs.create(project.id, "timeline_render", payload)
    observed: dict[str, object] = {}

    def fake_build(**kwargs):
        observed["timeline"] = kwargs["timeline"]
        observed["video_codec"] = kwargs["video_codec"]
        return ["ffmpeg", "-version"], 4.5

    def fake_render(**kwargs) -> None:
        observed["command"] = kwargs["command"]
        kwargs["output_path"].parent.mkdir(parents=True, exist_ok=True)
        kwargs["output_path"].write_bytes(b"edited-master")
        kwargs["on_progress"](0.5)

    monkeypatch.setattr(backend_app, "store", store)
    monkeypatch.setattr(backend_app, "jobs", jobs)
    monkeypatch.setattr(backend_app, "build_timeline_render_command", fake_build)
    monkeypatch.setattr(backend_app, "render_timeline_edited_master", fake_render)

    result = backend_app._run_timeline_render(project.id, job.id, payload)

    assert observed["timeline"] == payload["timeline"]
    assert observed["video_codec"] == "h264"
    assert observed["command"] == ["ffmpeg", "-version"]
    video_path = store.project_dir(project.id) / result["video"]
    manifest_path = store.project_dir(project.id) / result["artifact_manifest"]
    assert video_path.read_bytes() == b"edited-master"
    assert manifest_path.is_file()
    saved_project = store.get(project.id)
    assert saved_project is not None
    assert saved_project.meta["last_timeline_render"]["video"] == result["video"]
    assert saved_project.meta["last_timeline_render"]["status"] == "succeeded"
    assert saved_project.meta["outputs"]["videos"][-1]["kind"] == "timeline_render"
    saved_job = jobs.get(project.id, job.id)
    assert saved_job is not None
    assert saved_job.progress is not None
    assert saved_job.progress["stage"] == "complete"


def test_execute_job_dispatches_timeline_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ProjectStore(tmp_path / "data")
    jobs = JobStore(store.projects_dir)
    project = store.create("Timeline dispatch")
    job = jobs.create(project.id, "timeline_render", {"timeline": {}, "settings": {}})
    expected = {"video": "outputs/videos/master.mp4", "duration_s": 1.0}

    monkeypatch.setattr(backend_app, "store", store)
    monkeypatch.setattr(backend_app, "jobs", jobs)
    monkeypatch.setattr(backend_app, "_run_timeline_render", lambda *_args: expected)

    backend_app._execute_job(job)

    saved = jobs.get(project.id, job.id)
    assert saved is not None
    assert saved.status == "succeeded"
    assert saved.result == expected


def test_execute_timeline_job_converts_ffmpeg_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ProjectStore(tmp_path / "data")
    jobs = JobStore(store.projects_dir)
    project = store.create("Timeline cancel")
    payload = {
        "settings": {},
        "timeline": {"tracks": [{"clips": [{"source_path": "outputs/videos/clip.mp4"}]}]},
    }
    job = jobs.create(project.id, "timeline_render", payload)

    monkeypatch.setattr(backend_app, "store", store)
    monkeypatch.setattr(backend_app, "jobs", jobs)
    monkeypatch.setattr(
        backend_app,
        "build_timeline_render_command",
        lambda **_kwargs: (["ffmpeg"], 1.0),
    )
    monkeypatch.setattr(
        backend_app,
        "render_timeline_edited_master",
        lambda **_kwargs: (_ for _ in ()).throw(
            ffmpeg_service.TimelineRenderCanceled("Timeline render canceled")
        ),
    )

    backend_app._execute_job(job)

    saved = jobs.get(project.id, job.id)
    assert saved is not None
    assert saved.status == "canceled"
    assert saved.error is None
    saved_project = store.get(project.id)
    assert saved_project is not None
    assert "last_timeline_render_request" not in saved_project.meta


def test_timeline_worker_revalidates_render_settings(tmp_path: Path, monkeypatch) -> None:
    store = ProjectStore(tmp_path / "data")
    jobs = JobStore(store.projects_dir)
    project = store.create("Timeline validation")
    job = jobs.create(project.id, "timeline_render", {})
    monkeypatch.setattr(backend_app, "store", store)
    monkeypatch.setattr(backend_app, "jobs", jobs)

    with pytest.raises(ValidationError):
        backend_app._run_timeline_render(
            project.id,
            job.id,
            {"settings": {"width": 1279}, "timeline": {"tracks": []}},
        )
