from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
STUDIO_ROOT = REPO_ROOT / "studio" / "edmg-studio"
BACKEND_ROOT = STUDIO_ROOT / "python_backend"

SOURCE_SUFFIXES = {
    ".bat",
    ".cmd",
    ".cjs",
    ".js",
    ".mjs",
    ".ps1",
    ".py",
    ".sh",
    ".yaml",
    ".yml",
}
SOURCE_ROOTS = (
    REPO_ROOT / ".github" / "workflows",
    REPO_ROOT / "deployment",
    REPO_ROOT / "scripts",
    STUDIO_ROOT / "main-process",
    STUDIO_ROOT / "packaging",
    STUDIO_ROOT / "scripts",
    STUDIO_ROOT / "tools",
    BACKEND_ROOT / "edmg_studio_backend",
)

# These scripts provision independent, upstream sidecar environments. They do
# not mutate the backend project or a release environment, and are intentionally
# constrained to pinned-uv `uv pip --python` calls.
SIDECAR_UV_PIP_ALLOWLIST = {
    STUDIO_ROOT / "scripts" / "setup_linux_comfyui.sh",
    STUDIO_ROOT / "scripts" / "setup_linux_hf_bucket.sh",
    STUDIO_ROOT / "scripts" / "setup_linux_s3_model_cache.sh",
}

LEGACY_INSTALL_PATTERNS = (
    re.compile(
        r"\b(?:python(?:3(?:\.\d+)?)?|py(?:\.exe)?)\s+-m\s+pip\b", re.IGNORECASE
    ),
    re.compile(
        r"\b(?:python(?:3(?:\.\d+)?)?|py(?:\.exe)?)\s+-m\s+venv\b", re.IGNORECASE
    ),
    re.compile(r"\bpip(?:3)?\s+install\b", re.IGNORECASE),
    re.compile(r"[\"']-m[\"']\s*,\s*[\"']pip[\"']", re.IGNORECASE),
)

DYNAMIC_INDEX_NAMES = {
    "EDMG_BACKEND_TORCH_INDEX_URL",
    "EDMG_CUDA_WHEEL_INDEX",
    "EDMG_CUDA_WHEEL_TAG",
    "PIP_EXTRA_INDEX_URL",
    "PIP_FIND_LINKS",
    "PIP_INDEX_URL",
    "PIP_TORCH_INDEX_URL",
}


def _execution_sources() -> list[Path]:
    paths: set[Path] = {
        REPO_ROOT / "RUN_ME.bat",
        REPO_ROOT / "run_me.sh",
        STUDIO_ROOT / "RUN_ME.bat",
        STUDIO_ROOT / "run_me.sh",
        STUDIO_ROOT / "python_backend" / "Dockerfile",
    }
    for root in SOURCE_ROOTS:
        if not root.exists():
            continue
        for candidate in root.rglob("*"):
            if not candidate.is_file():
                continue
            if "tests" in candidate.parts or candidate.name.endswith(".test.mjs"):
                continue
            if (
                candidate.suffix.lower() in SOURCE_SUFFIXES
                or candidate.name == "Dockerfile"
            ):
                paths.add(candidate)
    return sorted(paths)


def _non_comment_lines(path: Path) -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    for number, line in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
    ):
        stripped = line.lstrip()
        if stripped.startswith(("#", "//", "/*", "*")):
            continue
        result.append((number, line))
    return result


def test_repository_uses_one_locked_python_project() -> None:
    assert (REPO_ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.12"
    assert (BACKEND_ROOT / "uv.lock").is_file()
    assert not (BACKEND_ROOT / "requirements-internal.txt").exists()
    assert not (BACKEND_ROOT / "requirements-directml.txt").exists()

    project = tomllib.loads(
        (BACKEND_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert project["project"]["requires-python"] == ">=3.12,<3.13"
    assert project["tool"]["uv"]["required-version"] == "==0.11.28"

    extras = project["project"]["optional-dependencies"]
    assert {
        "cpu",
        "directml",
        "cuda",
        "audio",
        "clap",
        "asr",
        "source-separation",
        "parakeet",
        "aws",
        "azure",
        "internal-video",
    } <= set(extras)
    assert "sentencepiece>=0.2,<1" in extras["internal-video"]
    assert len(project["tool"]["uv"]["conflicts"]) == 3

    groups = project["dependency-groups"]
    assert any(str(item).startswith("pytest") for item in groups["test"])
    assert any(str(item).startswith("httpx") for item in groups["test"])
    assert any(str(item).startswith("ruff") for item in groups["lint"])
    assert any(str(item).startswith("pyinstaller") for item in groups["build"])

    indexes = {item["name"]: item for item in project["tool"]["uv"]["index"]}
    assert indexes["pytorch-cpu"]["explicit"] is True
    assert indexes["pytorch-cu130"]["explicit"] is True


def test_supported_paths_do_not_invoke_pip_or_create_venvs() -> None:
    violations: list[str] = []
    for path in _execution_sources():
        matches: list[tuple[int, str]] = []
        for number, line in _non_comment_lines(path):
            if any(pattern.search(line) for pattern in LEGACY_INSTALL_PATTERNS):
                matches.append((number, line.strip()))

        if path in SIDECAR_UV_PIP_ALLOWLIST:
            for number, line in matches:
                if (
                    "UV_BIN" not in line
                    or "pip install" not in line
                    or "--python" not in line
                ):
                    violations.append(f"{path.relative_to(REPO_ROOT)}:{number}: {line}")
            continue

        violations.extend(
            f"{path.relative_to(REPO_ROOT)}:{number}: {line}"
            for number, line in matches
        )

    assert not violations, (
        "legacy dependency installation remains in supported paths:\n"
        + "\n".join(violations)
    )


def test_operational_documentation_uses_the_locked_toolchain() -> None:
    documents = {
        REPO_ROOT / "AGENTS.md",
        REPO_ROOT / "CONTRIBUTING.md",
        REPO_ROOT / "README.md",
        REPO_ROOT / "RELEASE.md",
        STUDIO_ROOT / "README.md",
        BACKEND_ROOT / "README.md",
    }
    documents.update(
        path
        for path in (REPO_ROOT / "docs").rglob("*.md")
        if "superpowers" not in path.parts and path.name != "UV_MIGRATION_INVENTORY.md"
    )
    documents.update((STUDIO_ROOT / "packaging").rglob("*.md"))
    documents.update((REPO_ROOT / "deployment").rglob("*.md"))

    violations: list[str] = []
    for path in sorted(documents):
        for number, line in _non_comment_lines(path):
            if any(pattern.search(line) for pattern in LEGACY_INSTALL_PATTERNS):
                violations.append(
                    f"{path.relative_to(REPO_ROOT)}:{number}: {line.strip()}"
                )
    assert not violations, (
        "operational documentation still advertises pip/venv commands:\n"
        + "\n".join(violations)
    )


def test_legacy_requirements_have_no_supported_consumers() -> None:
    violations: list[str] = []
    for path in _execution_sources():
        text = path.read_text(encoding="utf-8", errors="replace")
        for legacy_name in ("requirements-internal.txt", "requirements-directml.txt"):
            if legacy_name in text:
                violations.append(
                    f"{path.relative_to(REPO_ROOT)} consumes {legacy_name}"
                )
    assert not violations, "\n".join(violations)


def test_dynamic_dependency_indexes_are_rejected_not_consumed() -> None:
    guard = STUDIO_ROOT / "scripts" / "release-python-toolchain.mjs"
    guard_text = guard.read_text(encoding="utf-8")
    assert "assertNoDynamicDependencyOverrides" in guard_text
    for name in DYNAMIC_INDEX_NAMES:
        assert name in guard_text, f"release override guard is missing {name}"

    allowed_mentions = {
        guard,
        STUDIO_ROOT / "scripts" / "release-python-toolchain.node-test.mjs",
    }
    violations: list[str] = []
    for path in _execution_sources():
        if path in allowed_mentions:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for name in sorted(DYNAMIC_INDEX_NAMES):
            if name in text:
                violations.append(f"{path.relative_to(REPO_ROOT)} consumes {name}")
    assert not violations, "dynamic dependency/index inputs remain:\n" + "\n".join(
        violations
    )


def test_ci_and_release_paths_are_frozen() -> None:
    expected_fragments = {
        REPO_ROOT / ".github" / "workflows" / "studio.yml": (
            "uv lock --check",
            "uv sync --frozen --extra cpu",
            "uv sync --frozen --extra directml",
            "uv sync --frozen --extra cuda",
            "uv run --frozen",
            "pnpm run dist:linux",
            "Verify Linux AppImage",
            "autobuild-2026-07-26-13-28/ffmpeg-N-125773-g7002e01c19-linux64-gpl.tar.xz",
            "5abcecd8f7899cf5491cb8fac00767886cf433d9e96fb3c0065132a3daa8fcac",
        ),
        STUDIO_ROOT / "scripts" / "release-python-toolchain.mjs": (
            'return ["lock", "--check"]',
            'return ["sync", "--frozen"',
            '"run",\n    "--frozen",\n    "--no-sync"',
            "assertTrackedCleanDependencyStatus",
            "assertNoDynamicDependencyOverrides",
        ),
        STUDIO_ROOT / "scripts" / "prepare-release-bundle.mjs": (
            "uvLockCheckArgs()",
            "uvSyncArgs(profile)",
            "uvRunArgs(profile",
            "assertTrackedCleanDependencyStatus",
            "assertNoDynamicDependencyOverrides",
        ),
        STUDIO_ROOT / "main-process" / "backend-runtime.mjs": (
            '["run", "--frozen"',
            'label: "packaged-backend"',
        ),
        BACKEND_ROOT / "edmg_studio_backend" / "uv_toolchain.py": (
            '[uv, "lock", "--check"]',
            'args = [action, "--frozen"',
            "is_packaged_backend()",
        ),
        REPO_ROOT / "scripts" / "run_pytest_scopes.py": (
            '[uv, "lock", "--check"]',
            '"sync",\n            "--frozen"',
            '[uv, "run", *UV_PROJECT_FLAGS',
        ),
        BACKEND_ROOT / "edmg_studio_backend" / "integrations" / "lightning.py": (
            '"${{UV_BIN}}" lock --check',
            '"${{UV_BIN}}" sync --frozen',
            'exec "${{UV_BIN}}" run --frozen --no-sync',
        ),
    }
    for path, fragments in expected_fragments.items():
        text = path.read_text(encoding="utf-8")
        for fragment in fragments:
            assert fragment in text, (
                f"{path.relative_to(REPO_ROOT)} is missing frozen guard: {fragment}"
            )

    for dockerfile in (
        REPO_ROOT / "deployment" / "aws_batch" / "Dockerfile",
        REPO_ROOT / "deployment" / "hyperlift" / "ai-service.Dockerfile",
        REPO_ROOT / "deployment" / "hyperlift" / "backend.Dockerfile",
        BACKEND_ROOT / "Dockerfile",
    ):
        text = dockerfile.read_text(encoding="utf-8")
        assert "uv lock --check" in text
        assert "uv sync --frozen" in text
        command_text = re.sub(r"[\[\],\"']", " ", text)
        command_text = " ".join(command_text.split())
        assert re.search(
            r"\buv\s+run\b.{0,120}--frozen\b.{0,120}--no-sync\b", command_text
        )


def test_linux_launchers_honor_independent_storage_directories() -> None:
    expected_fragments = {
        STUDIO_ROOT / "scripts" / "start_lightning_backend.sh": (
            'EDMG_STUDIO_DATA_DIR="${EDMG_STUDIO_DATA_DIR:-',
            'EDMG_STUDIO_MODELS_DIR="${EDMG_STUDIO_MODELS_DIR:-',
            'EDMG_STUDIO_CACHE_DIR="${EDMG_STUDIO_CACHE_DIR:-',
            'EDMG_STUDIO_LOGS_DIR="${EDMG_STUDIO_LOGS_DIR:-',
            'EDMG_STUDIO_EXTERNAL_DIR="${EDMG_STUDIO_EXTERNAL_DIR:-',
        ),
        STUDIO_ROOT / "scripts" / "setup_linux_comfyui.sh": (
            'COMFY_ROOT="${COMFY_ROOT:-${EDMG_STUDIO_EXTERNAL_DIR}/ComfyUI}"',
            'COMFY_LOG_DIR="${COMFY_LOG_DIR:-${EDMG_STUDIO_LOGS_DIR}}"',
        ),
        STUDIO_ROOT / "scripts" / "setup_linux_ollama.sh": (
            'OLLAMA_MODELS="${OLLAMA_MODELS:-${EDMG_STUDIO_MODELS_DIR}/ollama}"',
            'OLLAMA_LOG_DIR="${OLLAMA_LOG_DIR:-${EDMG_STUDIO_LOGS_DIR}}"',
        ),
    }
    for path, fragments in expected_fragments.items():
        text = path.read_text(encoding="utf-8")
        for fragment in fragments:
            assert fragment in text, (
                f"{path.relative_to(REPO_ROOT)} is missing portable storage fragment: {fragment}"
            )


def test_linux_launchers_override_inherited_hugging_face_cache_paths() -> None:
    managed_hf_keys = (
        "HF_HOME",
        "HF_HUB_CACHE",
        "HF_XET_CACHE",
        "HF_ASSETS_CACHE",
        "HUGGINGFACE_HUB_CACHE",
        "HUGGINGFACE_ASSETS_CACHE",
        "TRANSFORMERS_CACHE",
    )
    expected_fragments = {
        STUDIO_ROOT / "scripts" / "start_lightning_backend.sh": {
            "HF_HOME": 'export HF_HOME="${EDMG_STUDIO_CACHE_DIR}/huggingface"',
            "HF_HUB_CACHE": 'export HF_HUB_CACHE="${HF_HOME}/hub"',
            "HF_XET_CACHE": 'export HF_XET_CACHE="${HF_HOME}/xet"',
            "HF_ASSETS_CACHE": 'export HF_ASSETS_CACHE="${HF_HOME}/assets"',
            "HUGGINGFACE_HUB_CACHE": 'export HUGGINGFACE_HUB_CACHE="${HF_HUB_CACHE}"',
            "HUGGINGFACE_ASSETS_CACHE": 'export HUGGINGFACE_ASSETS_CACHE="${HF_ASSETS_CACHE}"',
            "TRANSFORMERS_CACHE": 'export TRANSFORMERS_CACHE="${EDMG_STUDIO_CACHE_DIR}/transformers"',
        },
        STUDIO_ROOT / "edmg_gcp_gpu_bootstrap.sh": {
            "HF_HOME": "export HF_HOME=${HF_HOME_DIR}",
            "HF_HUB_CACHE": "export HF_HUB_CACHE=${HF_HUB_CACHE_DIR}",
            "HF_XET_CACHE": "export HF_XET_CACHE=${HF_XET_CACHE_DIR}",
            "HF_ASSETS_CACHE": "export HF_ASSETS_CACHE=${HF_ASSETS_CACHE_DIR}",
            "HUGGINGFACE_HUB_CACHE": "export HUGGINGFACE_HUB_CACHE=${HF_HUB_CACHE_DIR}",
            "HUGGINGFACE_ASSETS_CACHE": "export HUGGINGFACE_ASSETS_CACHE=${HF_ASSETS_CACHE_DIR}",
            "TRANSFORMERS_CACHE": "export TRANSFORMERS_CACHE=${TRANSFORMERS_CACHE_DIR}",
        },
        STUDIO_ROOT / "edmg_remote_reinstall_ports.sh": {
            "HF_HOME": 'export HF_HOME="$CACHE_DIR/huggingface"',
            "HF_HUB_CACHE": 'export HF_HUB_CACHE="$HF_HOME/hub"',
            "HF_XET_CACHE": 'export HF_XET_CACHE="$HF_HOME/xet"',
            "HF_ASSETS_CACHE": 'export HF_ASSETS_CACHE="$HF_HOME/assets"',
            "HUGGINGFACE_HUB_CACHE": 'export HUGGINGFACE_HUB_CACHE="$HF_HUB_CACHE"',
            "HUGGINGFACE_ASSETS_CACHE": 'export HUGGINGFACE_ASSETS_CACHE="$HF_ASSETS_CACHE"',
            "TRANSFORMERS_CACHE": 'export TRANSFORMERS_CACHE="$CACHE_DIR/transformers"',
        },
    }

    for path, fragments in expected_fragments.items():
        text = path.read_text(encoding="utf-8")
        assert set(fragments) == set(managed_hf_keys)
        for key, fragment in fragments.items():
            assert fragment in text, (
                f"{path.relative_to(REPO_ROOT)} must override inherited {key} from the selected cache root"
            )


def test_linux_ollama_setup_uses_pinned_verified_release_artifacts() -> None:
    script_path = STUDIO_ROOT / "scripts" / "setup_linux_ollama.sh"
    lock_path = STUDIO_ROOT / "scripts" / "setup_linux_ollama.lock.sh"
    script_text = script_path.read_text(encoding="utf-8")
    lock_text = lock_path.read_text(encoding="utf-8")

    assert 'source "${SCRIPT_DIR}/setup_linux_ollama.lock.sh"' in script_text
    assert 'OLLAMA_MODELS="${OLLAMA_MODELS:-${EDMG_STUDIO_MODELS_DIR}/ollama}"' in script_text
    assert 'OLLAMA_LOG_DIR="${OLLAMA_LOG_DIR:-${EDMG_STUDIO_LOGS_DIR}}"' in script_text
    assert 'OLLAMA_ALLOW_VERSION_OVERRIDE="${OLLAMA_ALLOW_VERSION_OVERRIDE:-0}"' in script_text
    assert "download_verified_file" in script_text
    assert "stop_tracked_pid" in script_text
    assert "OLLAMA_PID_FILE" in script_text
    assert "ollama.com/install.sh" not in script_text
    assert "pkill" not in script_text
    assert "/tmp/" not in script_text

    assert 'EDMG_LOCKED_OLLAMA_VERSION="v0.33.3"' in lock_text
    assert "EDMG_LOCKED_OLLAMA_SHA256_AMD64" in lock_text
    assert "EDMG_LOCKED_OLLAMA_SHA256_ARM64" in lock_text


def test_linux_comfyui_setup_pins_repos_and_uses_checked_in_snapshots() -> None:
    script_path = STUDIO_ROOT / "scripts" / "setup_linux_comfyui.sh"
    lock_path = STUDIO_ROOT / "scripts" / "setup_linux_comfyui.lock.sh"
    constraints_dir = STUDIO_ROOT / "scripts" / "constraints" / "comfyui"
    script_text = script_path.read_text(encoding="utf-8")
    lock_text = lock_path.read_text(encoding="utf-8")

    assert 'source "${SCRIPT_DIR}/setup_linux_comfyui.lock.sh"' in script_text
    assert 'CONSTRAINTS_DIR="${SCRIPT_DIR}/constraints/comfyui"' in script_text
    assert 'COMFY_ROOT="${COMFY_ROOT:-${EDMG_STUDIO_EXTERNAL_DIR}/ComfyUI}"' in script_text
    assert 'COMFY_LOG_DIR="${COMFY_LOG_DIR:-${EDMG_STUDIO_LOGS_DIR}}"' in script_text
    assert 'COMFY_ALLOW_VERSION_OVERRIDE="${COMFY_ALLOW_VERSION_OVERRIDE:-0}"' in script_text
    assert "sync_pinned_repo" in script_text
    assert "install_snapshot_requirements" in script_text
    assert "download_verified_model" in script_text
    assert "install.py" not in script_text
    assert "git pull" not in script_text
    assert "/tmp/" not in script_text

    for name in (
        "comfyui-requirements.txt",
        "comfyui-manager-requirements.txt",
        "comfyui-animatediff-evolved-requirements.txt",
        "comfyui-stable-video-diffusion-requirements.txt",
    ):
        snapshot = constraints_dir / name
        assert snapshot.is_file(), f"missing checked-in ComfyUI snapshot: {snapshot}"
        assert snapshot.read_text(encoding="utf-8").strip(), (
            f"empty ComfyUI snapshot: {snapshot}"
        )

    for marker in (
        "EDMG_LOCKED_COMFYUI_REF",
        "EDMG_LOCKED_COMFYUI_MANAGER_REF",
        "EDMG_LOCKED_ANIMATEDIFF_REF",
        "EDMG_LOCKED_SVD_NODE_REF",
        "EDMG_LOCKED_SDXL_BASE_SHA256",
        "EDMG_LOCKED_ANIMATEDIFF_MODEL_SHA256",
    ):
        assert marker in lock_text


def test_shell_uv_bootstrap_verifies_release_archives() -> None:
    helper = (STUDIO_ROOT / "scripts" / "uv_toolchain.sh").read_text(encoding="utf-8")
    assert "astral.sh/uv" not in helper
    assert "github.com/astral-sh/uv/releases/download/${EDMG_UV_VERSION}" in helper
    assert "actual_sha256" in helper
    for digest in (
        "e490a6464492183c5d4534a5527fb4440f7f2bb2f228162ad7e4afe076dc0224",
        "03e9fe0a81b0718d0bc84625de3885df6cc3f89a8b6af6121d6b9f6113fb6533",
        "2ad79983127ffca7d77b77ce6a24278d7e4f7b817a1acf72fea5f8124b4aac5e",
        "33540eb7c883ab857eff79bd5ac2aa31fe27b595abecb4a9c003a2c998447232",
    ):
        assert digest in helper


def test_generated_lightning_bundle_is_lock_derived(
    tmp_path: Path, monkeypatch
) -> None:
    module_path = BACKEND_ROOT / "edmg_studio_backend" / "integrations" / "lightning.py"
    spec = importlib.util.spec_from_file_location(
        "edmg_lightning_bundle_test", module_path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setenv("EDMG_BACKEND_ACCELERATOR_PROFILE", "cpu")
    output = tmp_path / "lightning-bundle"
    result = module.generate_lightning_bundle(str(output))

    assert result["ok"] is True
    assert not (output / "requirements.txt").exists()
    assert (output / ".python-version").read_text(encoding="utf-8").strip() == "3.12"
    assert (output / "uv.lock").read_bytes() == (BACKEND_ROOT / "uv.lock").read_bytes()

    startup = (output / "startup.sh").read_text(encoding="utf-8")
    assert "lock --check" in startup
    assert "sync --frozen" in startup
    assert "run --frozen --no-sync" in startup
    for key in (
        "HF_HOME",
        "HF_HUB_CACHE",
        "HF_XET_CACHE",
        "HF_ASSETS_CACHE",
        "HUGGINGFACE_HUB_CACHE",
        "HUGGINGFACE_ASSETS_CACHE",
        "TRANSFORMERS_CACHE",
    ):
        assert f"export {key}=" in startup

    manifest = json.loads(
        (output / "lightning-bundle-manifest.json").read_text(encoding="utf-8")
    )
    expected_hash = hashlib.sha256((BACKEND_ROOT / "uv.lock").read_bytes()).hexdigest()
    assert manifest == {
        "accelerator_profile": "cpu",
        "capability_extras": ["core", "audio", "asr", "internal-video", "aws"],
        "lock_sha256": expected_hash,
        "python": "3.12",
        "schema_version": 1,
        "uv_version": "0.11.28",
    }
