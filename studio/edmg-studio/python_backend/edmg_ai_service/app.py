from __future__ import annotations

import tempfile

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .config import Settings
from .provider_factory import build_provider
from .schemas import PlanRequest, PlanResponse, HealthResponse


settings = Settings()
provider = build_provider(settings)

app = FastAPI(title="EDMG AI Service", version="0.1.0")

# Helpful when calling from Electron/Gradio
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(ok=True, provider=provider.name, model=getattr(provider, "model", None))


@app.post("/v1/plan", response_model=PlanResponse)
def plan(req: PlanRequest) -> PlanResponse:
    return provider.plan(req)


@app.post("/v1/transcribe")
async def transcribe_audio(file: UploadFile = File(...), model_size: str = "small") -> dict:
    try:
        from .asr import transcribe
    except Exception as e:
        raise HTTPException(status_code=501, detail=str(e))

    suffix = "." + (file.filename.split(".")[-1] if file.filename and "." in file.filename else "wav")
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        text = transcribe(tmp_path, model_size=model_size)
        return {"text": text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/audio_features")
async def audio_features(file: UploadFile = File(...)) -> dict:
    try:
        from .audio import lightweight_audio_features
    except Exception as e:
        raise HTTPException(status_code=501, detail=str(e))

    suffix = "." + (file.filename.split(".")[-1] if file.filename and "." in file.filename else "wav")
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        feats = lightweight_audio_features(tmp_path)
        return feats
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
