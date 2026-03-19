# AWS Batch worker template (EDMG Studio)

This folder is a **starter template** for running EDMG Studio renders on AWS Batch.

## What it is
- A container image that runs a small worker process.
- The worker:
  1) downloads a project bundle from S3
  2) runs one or more queued jobs (tick loop)
  3) uploads outputs back to S3

## Assumptions
- You have an S3 bucket for *inputs* and one for *outputs* (can be the same bucket).
- You are using an AMI / compute environment with GPU if you plan to render with ComfyUI.
- ComfyUI can be:
  - baked into the image, or
  - started as a sidecar, or
  - accessed via a network endpoint.

## Files
- `Dockerfile` – minimal worker image
- `worker.py` – the Batch entrypoint
- `job_definition.json` – example Batch Job Definition snippet

## Environment variables
- `EDMG_S3_BUCKET_IN` – bucket containing the bundle zip
- `EDMG_S3_KEY_IN` – key for the bundle zip
- `EDMG_S3_BUCKET_OUT` – bucket to upload results
- `EDMG_S3_PREFIX_OUT` – prefix/folder for results
- `EDMG_BACKEND_URL` – backend base URL (default: http://127.0.0.1:7860)
- `EDMG_TICKS` – number of job ticks to run (default: 9999)

This is intentionally opinionated but minimal; adapt to your Batch setup.
