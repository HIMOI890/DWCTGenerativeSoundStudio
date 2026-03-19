from __future__ import annotations

import os
import time
import zipfile
from pathlib import Path
from typing import Any

import requests

try:
    import boto3
except Exception:
    boto3 = None


def _env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name)
    if v is None:
        if default is None:
            raise RuntimeError(f"Missing env var: {name}")
        return default
    return v


def s3_download(bucket: str, key: str, out_path: Path) -> None:
    if boto3 is None:
        raise RuntimeError("boto3 not installed in image")
    s3 = boto3.client("s3")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    s3.download_file(bucket, key, str(out_path))


def s3_upload_dir(bucket: str, prefix: str, root: Path) -> None:
    if boto3 is None:
        raise RuntimeError("boto3 not installed in image")
    s3 = boto3.client("s3")
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        s3.upload_file(str(p), bucket, f"{prefix.rstrip('/')}/{rel}")


def tick(backend_url: str) -> dict[str, Any]:
    r = requests.post(f"{backend_url.rstrip('/')}/v1/jobs/tick", timeout=600)
    r.raise_for_status()
    return r.json()


def main() -> None:
    bucket_in = _env("EDMG_S3_BUCKET_IN")
    key_in = _env("EDMG_S3_KEY_IN")
    bucket_out = _env("EDMG_S3_BUCKET_OUT", bucket_in)
    prefix_out = _env("EDMG_S3_PREFIX_OUT", "edmg_outputs/")

    backend_url = _env("EDMG_BACKEND_URL", "http://127.0.0.1:7860")
    ticks = int(_env("EDMG_TICKS", "9999"))

    work = Path("/data")
    work.mkdir(parents=True, exist_ok=True)

    bundle_zip = work / "bundle.zip"
    print(f"Downloading s3://{bucket_in}/{key_in} -> {bundle_zip}")
    s3_download(bucket_in, key_in, bundle_zip)

    print("Unpacking bundle")
    with zipfile.ZipFile(bundle_zip, "r") as z:
        z.extractall(work)

    # NOTE: this template assumes the Studio backend runs in the same container or is reachable.
    # If you want fully self-contained Batch jobs, bake ComfyUI into the image and start the backend here.

    print(f"Ticking worker against {backend_url}")
    for i in range(ticks):
        res = tick(backend_url)
        note = res.get("note")
        if note == "no queued jobs":
            print("No queued jobs, stopping")
            break
        print(f"tick {i+1}: {res.get('job', {}).get('id')} {res.get('job', {}).get('status')}")
        time.sleep(0.25)

    # Upload outputs (best-effort)
    out_root = work
    print(f"Uploading results to s3://{bucket_out}/{prefix_out}")
    s3_upload_dir(bucket_out, prefix_out, out_root)
    print("Done")


if __name__ == "__main__":
    main()
