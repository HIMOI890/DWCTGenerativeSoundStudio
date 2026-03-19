from __future__ import annotations


def transcribe(path: str, model_size: str = "small") -> str:
    """Transcribe audio to text using optional faster-whisper (CPU-friendly).

    Install:
      pip install -e ".[asr]"
    """
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except Exception as e:
        raise RuntimeError("ASR requires optional deps: pip install -e '.[asr]'") from e

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(path)
    return "\n".join(seg.text.strip() for seg in segments if seg.text and seg.text.strip())
