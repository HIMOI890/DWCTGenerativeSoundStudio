from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import requests

try:
    from huggingface_hub import snapshot_download  # type: ignore
except Exception:  # pragma: no cover
    snapshot_download = None  # type: ignore

from ..errors import UserFacingError
from .setup_wizard import _ollama_base  # reuse
from .model_catalog import built_in_catalog, built_in_packs
from ..services.setup_wizard import comfy_portable_installed, comfy_portable_root
from .secrets import SecretStore


# ------------------------------ persistence ------------------------------

def _config_dir(data_dir: Path) -> Path:
    p = (data_dir / "config").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p

def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return default

def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# ------------------------------ tasks ------------------------------

@dataclass
class ModelTask:
    id: str
    name: str
    status: str = "queued"  # queued|running|done|failed
    progress: Optional[float] = None
    last_log: str = ""
    error: Optional[str] = None
    started_at: Optional[float] = None
    ended_at: Optional[float] = None
    model_id: Optional[str] = None


class ModelTaskManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._tasks: dict[str, ModelTask] = {}

    def list(self) -> list[ModelTask]:
        with self._lock:
            return sorted(self._tasks.values(), key=lambda t: (t.started_at or 0), reverse=True)

    def start(self, name: str, fn, *args, **kwargs) -> ModelTask:
        task = ModelTask(id=str(uuid.uuid4())[:8], name=name, status="queued")
        with self._lock:
            self._tasks[task.id] = task

        def runner():
            task.status = "running"
            task.started_at = time.time()
            try:
                fn(task, *args, **kwargs)
                task.status = "done"
            except Exception as e:
                task.status = "failed"
                task.error = str(e)
                task.last_log = (task.last_log + "\n" if task.last_log else "") + f"ERROR: {e}"
            finally:
                task.ended_at = time.time()

        threading.Thread(target=runner, daemon=True).start()
        return task

    @staticmethod
    def log(task: ModelTask, msg: str) -> None:
        task.last_log = msg

    @staticmethod
    def set_progress(task: ModelTask, v: Optional[float]) -> None:
        task.progress = v


# ------------------------------ manager ------------------------------

class ModelManager:
    def __init__(self, data_dir: Path, comfyui_url: str, ollama_url: str, secrets: SecretStore | None = None):
        self.data_dir = data_dir
        self.comfyui_url = comfyui_url.rstrip("/")
        self.ollama_url = _ollama_base(ollama_url)
        self.secrets = secrets
        self.tasks = ModelTaskManager()

        cfg = _config_dir(self.data_dir)
        self._user_models_path = cfg / "models_user.json"
        self._accept_path = cfg / "licenses_accepted.json"

        self._lock = threading.Lock()

    # ---- catalog ----
    def catalog(self) -> dict[str, Any]:
        built = built_in_catalog()
        user = _read_json(self._user_models_path, default=[])
        if not isinstance(user, list):
            user = []
        accepted = _read_json(self._accept_path, default={})
        if not isinstance(accepted, dict):
            accepted = {}

        installed = self._installed_map(built + user)

        return {
            "catalog": built,
            "user": user,
            "packs": built_in_packs(),
            "accepted": accepted,
            "installed": installed,
        }

    # ---- acceptance ----
    def accept_license(self, model_id: str, license_id: str) -> None:
        if not model_id or not license_id:
            raise UserFacingError("Missing model_id or license_id", hint="Select a model and accept its license terms.")
        data = _read_json(self._accept_path, default={})
        if not isinstance(data, dict):
            data = {}
        data[model_id] = {
            "license_id": license_id,
            "accepted_at": time.time(),
        }
        _write_json(self._accept_path, data)

    def _is_accepted(self, model_id: str) -> bool:
        data = _read_json(self._accept_path, default={})
        return isinstance(data, dict) and model_id in data

    # ---- add/remove user models ----
    def add_user_model(self, entry: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(entry, dict) or not entry.get("id"):
            raise UserFacingError("Invalid model entry", hint="Provide a valid model entry.")
        with self._lock:
            user = _read_json(self._user_models_path, default=[])
            if not isinstance(user, list):
                user = []
            # replace if exists
            user = [u for u in user if isinstance(u, dict) and u.get("id") != entry["id"]]
            user.append(entry)
            _write_json(self._user_models_path, user)
        return entry

    def remove_user_model(self, model_id: str) -> None:
        with self._lock:
            user = _read_json(self._user_models_path, default=[])
            if not isinstance(user, list):
                return
            user2 = [u for u in user if isinstance(u, dict) and u.get("id") != model_id]
            _write_json(self._user_models_path, user2)

    # ---- install ----
    def install(self, model_id: str) -> ModelTask:
        cat = self.catalog()
        all_entries = (cat.get("catalog") or []) + (cat.get("user") or [])
        entry = next((e for e in all_entries if isinstance(e, dict) and e.get("id") == model_id), None)
        if not entry:
            raise UserFacingError(f"Unknown model id: {model_id}", hint="Refresh the model catalog and try again.")

        # Enforce license acceptance for any external weights/download.
        if entry.get("kind") != "llm" and not self._is_accepted(model_id):
            raise UserFacingError(
                "License not accepted",
                hint="Open Model Manager, click the model, review license, then click Accept & Install."
            )

        source = (entry.get("source") or "").lower()
        if source == "ollama":
            name = f"Install (Ollama): {entry.get('name')}"
            return self.tasks.start(name, self._install_ollama, entry)
        if source in ("hf", "civitai", "local"):
            name = f"Install: {entry.get('name')}"
            return self.tasks.start(name, self._install_file_model, entry)

        raise UserFacingError("Unsupported model source", hint=f"Source '{source}' is not supported yet.")

    def install_pack(self, pack_id: str) -> list[ModelTask]:
        packs = built_in_packs()
        pack = next((p for p in packs if p.get("id") == pack_id), None)
        if not pack:
            raise UserFacingError("Unknown pack", hint="Choose a valid pack.")
        tasks: list[ModelTask] = []
        for mid in (pack.get("models") or []):
            tasks.append(self.install(mid))
        return tasks


    # ---- resolution ----
    def _internal_models_dir(self, folder: str) -> Path:
        root = (self.data_dir / "models_internal" / folder).resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _models_dest(self, entry: dict[str, Any]) -> tuple[str, Path]:
        """Return (mode, dest_path).

        mode:
          - "file": download/copy a single file into dest_path
          - "snapshot": download a HF repo snapshot into dest_path (directory)
        """
        target = entry.get("target") or {}
        engine = (target.get("engine") if isinstance(target, dict) else "") or "comfyui"
        folder = (target.get("folder") if isinstance(target, dict) else None) or "checkpoints"
        fname = str(entry.get("filename") or "")

        if engine == "internal":
            # Diffusers expects a directory repo snapshot.
            model_dir = self._internal_models_dir(folder) / str(entry.get("id") or "model")
            return "snapshot", model_dir

        # default: comfyui file model
        if not fname:
            fname = "model.safetensors"
        return "file", self._comfy_models_dir(folder) / fname

    # ---- resolution ----
    def _comfy_models_dir(self, folder: str) -> Path:
        # Prefer ComfyUI Portable (installed via setup wizard)
        if comfy_portable_installed(self.data_dir):
            root = comfy_portable_root(self.data_dir) / "ComfyUI" / "models" / folder
            root.mkdir(parents=True, exist_ok=True)
            return root
        # Otherwise, use local data dir as a staging area
        root = (self.data_dir / "models" / folder).resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _installed_map(self, entries: list[dict[str, Any]]) -> dict[str, bool]:
        out: dict[str, bool] = {}
        # for ollama, fetch tags once
        ollama_models: set[str] = set()
        try:
            r = requests.get(f"{self.ollama_url}/api/tags", timeout=5)
            if r.ok:
                data = r.json() or {}
                for m in (data.get("models") or []):
                    if isinstance(m, dict) and m.get("name"):
                        ollama_models.add(str(m["name"]))
        except Exception:
            pass

        for e in entries:
            mid = str(e.get("id") or "")
            if not mid:
                continue
            src = (e.get("source") or "").lower()
            if src == "ollama":
                out[mid] = str(e.get("ollama_model") or "") in ollama_models
            else:
                fname = e.get("filename") or ""
                target = (e.get("target") or {})
                folder = (target.get("folder") if isinstance(target, dict) else None) or "checkpoints"
                if fname:
                    out[mid] = (self._comfy_models_dir(folder) / fname).exists()
                else:
                    out[mid] = False
        return out

    # ---- installers ----
    def _install_ollama(self, task: ModelTask, entry: dict[str, Any]) -> None:
        model = str(entry.get("ollama_model") or "")
        if not model:
            raise RuntimeError("Missing ollama_model")
        ModelTaskManager.log(task, f"Pulling {model} via Ollama…")
        with requests.post(
            f"{self.ollama_url}/api/pull",
            json={"model": model, "stream": True},
            stream=True,
            timeout=60 * 60,
        ) as r:
            r.raise_for_status()
            last = ""
            for line in r.iter_lines(decode_unicode=True):
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                status = obj.get("status") or ""
                total = obj.get("total")
                completed = obj.get("completed")
                if total and completed:
                    try:
                        p = float(completed) / float(total)
                        ModelTaskManager.set_progress(task, max(0.0, min(0.99, p)))
                    except Exception:
                        pass
                if status and status != last:
                    ModelTaskManager.log(task, status)
                    last = status
        ModelTaskManager.set_progress(task, 1.0)
        ModelTaskManager.log(task, "Done.")

    def _download_stream(self, task: ModelTask, url: str, dest: Path, headers: Optional[dict[str, str]] = None) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        headers = headers or {}
        ModelTaskManager.log(task, f"Downloading…\n{url}")
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        if tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass
        with requests.get(url, stream=True, timeout=60 * 60, headers=headers) as r:
            if r.status_code in (401, 403):
                raise UserFacingError(
                    "Download unauthorized",
                    hint="Set an API token in Settings → Tokens (Hugging Face token for HF downloads, Civitai API key for Civitai downloads), then retry."
                )
            r.raise_for_status()
            total = int(r.headers.get("content-length") or 0)
            got = 0
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    f.write(chunk)
                    got += len(chunk)
                    if total:
                        ModelTaskManager.set_progress(task, max(0.0, min(0.99, got / total)))
                f.flush()
                os.fsync(f.fileno())
        os.replace(tmp, dest)
        ModelTaskManager.set_progress(task, 1.0)
        ModelTaskManager.log(task, f"Saved: {dest.name}")

    def _install_file_model(self, task: ModelTask, entry: dict[str, Any]) -> None:
        src = (entry.get("source") or "").lower()
        kind = (entry.get("kind") or "").lower()
        target = entry.get("target") or {}
        folder = (target.get("folder") if isinstance(target, dict) else None) or "checkpoints"
        fname = str(entry.get("filename") or "")
        if not fname:
            # for civitai user entries we may set filename later
            fname = "model.safetensors"

        mode, dest = self._models_dest(entry)

        headers: dict[str, str] = {}
        # optional HF token support (prefer SecretStore; fall back to env vars)
        hf_token = ""
        if self.secrets is not None:
            hf_token = self.secrets.get("hf_token") or ""
        if not hf_token:
            hf_token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN") or ""
        if hf_token:
            headers["Authorization"] = f"Bearer {hf_token}"

        civitai_key = ""
        if self.secrets is not None:
            civitai_key = self.secrets.get("civitai_api_key") or ""
        if not civitai_key:
            civitai_key = os.getenv("CIVITAI_API_KEY") or ""

        if src == "hf":
            repo_id = str(entry.get("hf_repo_id") or entry.get("hf_repo") or "")
            url = str(entry.get("hf_url") or "")
            if mode == "snapshot":
                if not repo_id:
                    raise RuntimeError("Missing hf_repo_id for snapshot install")
                if snapshot_download is None:
                    raise RuntimeError("huggingface_hub is not installed (required for snapshot downloads)")
                dest.mkdir(parents=True, exist_ok=True)
                ModelTaskManager.log(task, f"Downloading HF snapshot: {repo_id}")
                snapshot_download(
                    repo_id=repo_id,
                    local_dir=str(dest),
                    local_dir_use_symlinks=False,
                    revision=str(entry.get("hf_revision") or "") or None,
                    token=(hf_token or None),
                    resume_download=True,
                )
                ModelTaskManager.set_progress(task, 1.0)
                return
            # file mode
            if not url:
                raise RuntimeError("Missing hf_url")
            self._download_stream(task, url, dest, headers=headers)
            return

        if src == "civitai":
            dl = str(entry.get("civitai_download_url") or "")
            if not dl:
                raise RuntimeError("Missing civitai_download_url")
            if civitai_key:
                headers["Authorization"] = f"Bearer {civitai_key}"
            self._download_stream(task, dl, dest, headers=headers)
            return

        if src == "local":
            # local models are assumed already placed. Copy if source_path provided.
            sp = str(entry.get("source_path") or "")
            if not sp:
                raise RuntimeError("Missing source_path")
            srcp = Path(sp).expanduser()
            if not srcp.exists():
                raise RuntimeError(f"File not found: {srcp}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(srcp.read_bytes())
            ModelTaskManager.log(task, f"Copied: {srcp.name}")
            ModelTaskManager.set_progress(task, 1.0)
            return

        raise RuntimeError(f"Unsupported source: {src}")

    
    def installed_path(self, model_id: str) -> Path | None:
        """Return local path for an installed model (file or directory), else None."""
        cat = self.catalog()
        all_entries = (cat.get("catalog") or []) + (cat.get("user") or [])
        entry = next((e for e in all_entries if isinstance(e, dict) and e.get("id") == model_id), None)
        if not entry:
            return None
        target = entry.get("target") or {}
        engine = (target.get("engine") if isinstance(target, dict) else "") or "comfyui"
        folder = (target.get("folder") if isinstance(target, dict) else None) or "checkpoints"
        if engine == "internal":
            p = (self._internal_models_dir(folder) / model_id)
            return p if p.exists() else None
        fname = str(entry.get("filename") or "")
        if not fname:
            return None
        p = self._comfy_models_dir(folder) / fname
        return p if p.exists() else None


    def import_local(self, file_path: str, name: str | None = None, folder: str = "checkpoints") -> dict[str, Any]:
        """Register a local model file and copy it into the configured ComfyUI models folder.

        This is the BYO path for checkpoints/loras/etc.
        """
        srcp = Path(file_path).expanduser()
        if not srcp.exists() or not srcp.is_file():
            raise UserFacingError("File not found", hint="Pick a valid local model file.")
        folder = (folder or "checkpoints").strip().lower()
        safe_folder = folder if folder in ("checkpoints","loras","embeddings","vae","controlnet","upscale_models") else "checkpoints"
        dest_dir = self._comfy_models_dir(safe_folder)
        dest = dest_dir / srcp.name
        dest.write_bytes(srcp.read_bytes())

        entry = {
            "id": f"local_{uuid.uuid4().hex[:8]}",
            "name": name or srcp.stem,
            "kind": safe_folder.rstrip("s") if safe_folder.endswith("s") else safe_folder,
            "source": "local",
            "source_path": str(dest),
            "filename": srcp.name,
            "target": {"engine": "comfyui", "folder": safe_folder},
            "license_id": "user-provided",
            "license_url": "",
            "redistributable_in_installer": False,
            "recommended": "advanced",
            "notes": "User-provided local file. Ensure you have rights to use/distribute outputs as applicable.",
        }
        self.add_user_model(entry)
        return entry

    # ---- civitai helper ----
    def civitai_import(self, url_or_id: str) -> dict[str, Any]:
        """Import a model from Civitai by URL or numeric modelId.

        We add an entry to the user model registry but DO NOT download until user clicks Install.
        """
        model_id, version_id = _parse_civitai_url(url_or_id)
        if not model_id:
            raise UserFacingError("Couldn't parse Civitai model URL/ID", hint="Paste a Civitai model URL like https://civitai.com/models/12345 or a numeric ID.")
        api_key = ""
        if self.secrets is not None:
            api_key = self.secrets.get("civitai_api_key") or ""
        if not api_key:
            api_key = os.getenv("CIVITAI_API_KEY") or ""
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        # Fetch model metadata
        r = requests.get(f"https://civitai.com/api/v1/models/{model_id}", headers=headers, timeout=30)
        if r.status_code in (401, 403):
            raise UserFacingError(
                "Civitai API unauthorized",
                hint="Set CIVITAI_API_KEY in Settings → Tokens (some downloads require auth), then retry."
            )
        r.raise_for_status()
        m = r.json() or {}
        name = m.get("name") or f"Civitai Model {model_id}"
        mtype = (m.get("type") or "").lower()  # Checkpoint, LORA, TextualInversion, etc.

        # Pick a version (latest by createdAt)
        versions = m.get("modelVersions") or []
        if version_id:
            v = next((vv for vv in versions if str(vv.get("id")) == str(version_id)), None)
        else:
            v = None
            if versions and isinstance(versions, list):
                versions_sorted = sorted(
                    [vv for vv in versions if isinstance(vv, dict)],
                    key=lambda vv: vv.get("createdAt") or "",
                    reverse=True,
                )
                v = versions_sorted[0] if versions_sorted else None
        if not v:
            raise UserFacingError("No model version found", hint="Try a different model or specify a version.")

        # Determine download URL + filename from primary file
        files = v.get("files") or []
        primary = None
        for f in files:
            if isinstance(f, dict) and f.get("primary"):
                primary = f
                break
        if not primary and files:
            primary = files[0]
        if not primary:
            # Some versions include top-level downloadUrl
            dl = v.get("downloadUrl")
            if not dl:
                raise UserFacingError("No downloadable file found", hint="This model may require login/API key to download.")
            fname = f"civitai_{model_id}_{v.get('id')}.safetensors"
        else:
            dl = primary.get("downloadUrl") or v.get("downloadUrl")
            # Safety: avoid pickle tensors by default.
            meta = primary.get("metadata") or {}
            fmt = str(meta.get("format") or "").lower()
            if fmt and "safetensor" not in fmt:
                raise UserFacingError(
                    "Unsafe model format blocked",
                    hint="This Civitai file is not a SafeTensor. Choose a SafeTensor variant or export/download manually."
                )

            fname = primary.get("name") or f"civitai_{model_id}_{v.get('id')}.safetensors"

        # Map to comfy folder
        folder = "checkpoints"
        if "lora" in mtype:
            folder = "loras"
        elif "textualinversion" in mtype or "embedding" in mtype:
            folder = "embeddings"
        elif "vae" in mtype:
            folder = "vae"
        elif "controlnet" in mtype:
            folder = "controlnet"

        entry = {
            "id": f"civitai_{model_id}_{v.get('id')}",
            "name": f"{name} (Civitai)",
            "kind": "checkpoint" if folder == "checkpoints" else folder.rstrip("s"),
            "source": "civitai",
            "civitai_model_id": model_id,
            "civitai_version_id": v.get("id"),
            "civitai_page_url": f"https://civitai.com/models/{model_id}",
            "civitai_download_url": dl,
            "filename": fname,
            "target": {"engine": "comfyui", "folder": folder},
            # Civitai license varies per model; we surface the page and mark as unknown unless the API returns license data.
            "license_id": str(m.get("license") or m.get("licenseId") or "unknown"),
            "license_url": f"https://civitai.com/models/{model_id}",
            "redistributable_in_installer": False,
            "recommended": "advanced",
            "notes": "Community model from Civitai. Review license/terms on the model page before using commercially.",
        }
        self.add_user_model(entry)
        return entry


def _parse_civitai_url(s: str) -> tuple[str | None, str | None]:
    s = (s or "").strip()
    if not s:
        return None, None
    if s.isdigit():
        return s, None

    # URLs like:
    #  - https://civitai.com/models/12345
    #  - https://civitai.com/models/12345/name?modelVersionId=67890
    m = re.search(r"civitai\.com/(?:en/)?models/(\d+)", s)
    model_id = m.group(1) if m else None
    mv = re.search(r"modelVersionId=(\d+)", s)
    version_id = mv.group(1) if mv else None
    return model_id, version_id
