EDMG Full Installation + Use Guide (Start-to-Finish)

This guide assumes you have the FULL bundle you uploaded: enhanced_deforum_music_generator_FULL.zip. Inside it are multiple builds. The most complete one (GUI installer + CLI orchestrator + ComfyUI workflow tools + HF video tools + docs) is:

✅ enhanced-deforum-music-generator_with_bootstrap_gui_v8.zip

1) Pick the right ZIP from the FULL bundle

Unzip enhanced_deforum_music_generator_FULL.zip

Extract this ZIP into a folder you control (no spaces recommended):

enhanced-deforum-music-generator_with_bootstrap_gui_v8.zip

You should end up with a folder like:

enhanced-deforum-music-generator/
Inside it you’ll see: installer_gui.py, bootstrap_all.py, setup.py, deploy.py, custom.yaml, scripts/, src/, etc.

The other ZIPs are older snapshots or variants (CPU-only, model catalogs, etc.). Use v8 as your “main”.

2) Prerequisites (install once)
Required

Python 3.10+ (3.11 usually OK)

Git (required if you want EDMG to auto-clone A1111/ComfyUI)

Strongly recommended

FFmpeg (transcription + many audio/video utilities rely on it)

macOS

Python: from python.org or Homebrew

Git: Xcode Command Line Tools

FFmpeg:

brew install ffmpeg


Ubuntu/Debian

sudo apt-get update
sudo apt-get install -y git ffmpeg python3-venv


Windows

Install Python from python.org (check “Add to PATH”)

Install Git for Windows

FFmpeg: install and add to PATH

3) Full install (GUI automatic installer) ✅ recommended

From inside the extracted enhanced-deforum-music-generator/ folder:

python installer_gui.py


In the GUI:

Enable CPU-only mode (if you don’t want CUDA/GPU reliance).

Click Install EDMG (Full)

Creates a local venv and installs all Python dependencies.

Optional: click Install A1111 + Deforum

Optional: click Install ComfyUI

Click Start EDMG API and/or Start EDMG UI

Go to Verify tab and run checks (it probes endpoints and tells you what’s up).

4) Full install (CLI orchestration) alternative

If you prefer terminal automation:

Install EDMG
python bootstrap_all.py install --edmg --full

Optional: install backends (best effort)
python bootstrap_all.py install --a1111 --deforum --cpu-only
python bootstrap_all.py install --comfyui --cpu-only

Run services
python bootstrap_all.py run edmg-api --host 127.0.0.1 --port 8000
python bootstrap_all.py run edmg-ui
python bootstrap_all.py run a1111 --cpu-only
python bootstrap_all.py run comfyui --cpu-only

Verify
python bootstrap_all.py verify \
  --edmg http://127.0.0.1:8000 \
  --a1111 http://127.0.0.1:7860 \
  --comfyui http://127.0.0.1:8188

5) CPU-only mode (no CUDA reliance)
Enable CPU-only globally

macOS/Linux

export EDMG_CPU_ONLY=1


Windows PowerShell

$env:EDMG_CPU_ONLY="1"

Enable CPU sanity preset (recommended)

This keeps video generation settings small enough to finish on CPU.
macOS/Linux

export EDMG_CPU_SANE=1


Windows PowerShell

$env:EDMG_CPU_SANE="1"

6) How to USE EDMG (core workflow)
6.1 Start the EDMG UI

From GUI: click Start EDMG UI, or run:

PYTHONPATH=./src python -m enhanced_deforum_music_generator.enhanced_deforum_music_generator ui


The UI port is set in custom.yaml:

interface:
  server_port: 7861


Open your browser to:

http://127.0.0.1:7861

6.2 In the UI: Analyze → Generate package

Typical flow:

Load your audio file

Click Analyze

Fill prompts:

Base prompt (what you want)

Style prompt (look/feel)

Negative prompt (things to avoid)

Set image params (width/height/fps/steps/cfg/seed/sampler/batch name)

Click Generate settings/package

✅ EDMG outputs a downloadable ZIP package containing:

deforum_settings.json

analysis_report.json

README.md

That ZIP is what you take into Deforum / other pipelines.

7) Using EDMG output with Automatic1111 + Deforum
7.1 Start A1111 with API enabled

If you started it via GUI/bootstraps, it should be under:

external/stable-diffusion-webui

Start it (CPU-only if desired):

python bootstrap_all.py run a1111 --cpu-only


A1111 should be at:

http://127.0.0.1:7860

7.2 Add at least one SD checkpoint

EDMG does not ship model weights.
In A1111, you typically put checkpoints under:

external/stable-diffusion-webui/models/Stable-diffusion/

Then refresh in the UI.

7.3 Import EDMG settings into Deforum

In A1111 WebUI, open the Deforum tab/extension

Look for import/load settings (varies by Deforum build)

Import deforum_settings.json from the EDMG package zip

Confirm soundtrack path and output settings

Render

Reality: some Deforum builds don’t expose a stable REST “run deforum” endpoint. EDMG’s guarantee is: you always get the correct settings JSON to import.

8) Using EDMG with ComfyUI
8.1 Install and start ComfyUI

Installed by bootstrap into:

external/ComfyUI

Run CPU:

python bootstrap_all.py run comfyui --cpu-only


ComfyUI default URL:

http://127.0.0.1:8188

8.2 Fetch ComfyUI workflow JSONs
python scripts/fetch_comfyui_workflows.py --out comfyui_workflows/downloaded

8.3 Wire required models into ComfyUI folders

If you keep models in a shared store like external/models:

python scripts/wire_comfyui_models.py \
  --comfyui-root external/ComfyUI \
  --models-root external/models \
  --workflow comfyui_workflows/downloaded/<workflow>.json


If symlinks fail (Windows often), use copy mode:

python scripts/wire_comfyui_models.py ... --mode copy

8.4 Open workflow in ComfyUI

In ComfyUI web UI:

Workflows → Open → select the JSON
(or drag-drop the workflow JSON)

Then adapt nodes to consume your EDMG schedule/settings (depending on workflow design).

9) Hugging Face video models (local, open models)

EDMG includes helpers and docs for Diffusers-format HF video models.

9.1 Run a video model (Diffusers runner)

Example (CPU):

python scripts/run_video_diffusers.py \
  --model-id THUDM/CogVideoX-5b \
  --prompt "cinematic drummer, stage lights" \
  --output outputs/out.mp4 \
  --device cpu \
  --cpu-sane


If the model is image-to-video, add:

  --image path/to/input.png


CPU video generation can be extremely slow. Use --cpu-sane to keep it feasible.

10) Benchmark multiple models with one prompt
python scripts/video_model_bench.py \
  --prompt "A macro shot of raindrops on neon glass, cinematic lighting" \
  --bench-name smoke \
  --device cpu \
  --cpu-sane \
  --quick


It writes outputs under:

outputs/smoke/

11) JUCE ↔ EDMG bridge (DAW/plugin/audio app integration)
11.1 Start EDMG API
PYTHONPATH=./src python -m uvicorn enhanced_deforum_music_generator.api.main:app --host 127.0.0.1 --port 8000

11.2 JUCE side concept

JUCE should:

make HTTP calls on a background thread

call EDMG endpoints:

GET /health/

analysis/generation endpoints (depending on build)

parse JSON and drive UI/automation

If your bundle includes juce_bridge/, follow the included JUCE docs in that folder.

12) Verify installation is healthy
Self-check

In v8, there’s a selfcheck.py. Run:

python selfcheck.py

API health

Once API is running:

http://127.0.0.1:8000/health/

Tests
pytest -q

13) Common problems
“FFmpeg not found”

Install ffmpeg and ensure it’s on PATH, then rerun.

“Port already in use”

Change ports in custom.yaml:

EDMG UI: interface.server_port

EDMG API: uvicorn --port

A1111: --port

ComfyUI: --port

“Windows symlink permission error”

Use --mode copy when wiring models, or run as Administrator / enable Developer Mode.

“CPU-only is slow”

That’s normal. Use:

EDMG_CPU_SANE=1

smaller duration/resolution

fewer steps/frames

14) What to do first (if you only do one path)

Extract enhanced-deforum-music-generator_with_bootstrap_gui_v8.zip

Run:

python installer_gui.py


Install EDMG (Full)

Start EDMG UI

Generate your first deforum_settings.json package

Then decide:

import into Deforum in A1111

or feed into ComfyUI workflows

or use Diffusers runner for open HF video models

If you want, tell me your OS (Windows/macOS/Linux) and whether you want A1111 or ComfyUI as your main renderer, and I’ll give you a “single straight-line path” with exactly the buttons/commands to click in order.