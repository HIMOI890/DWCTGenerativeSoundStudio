from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Any

def _require_boto3():
    try:
        import boto3  # type: ignore
        return boto3
    except Exception as e:
        raise RuntimeError("AWS integration requires optional deps: pip install -e '.[aws]'") from e

@dataclass
class AwsTestResult:
    ok: bool
    account: str | None = None
    region: str | None = None

def test_credentials(bucket: Optional[str] = None) -> AwsTestResult:
    boto3 = _require_boto3()
    sts = boto3.client("sts")
    ident = sts.get_caller_identity()
    account = ident.get("Account")
    region = boto3.session.Session().region_name
    if bucket:
        s3 = boto3.client("s3")
        s3.head_bucket(Bucket=bucket)
    return AwsTestResult(ok=True, account=account, region=region)

def upload_file_s3(bucket: str, key: str, path: str) -> dict[str, Any]:
    boto3 = _require_boto3()
    s3 = boto3.client("s3")
    s3.upload_file(path, bucket, key)
    return {"bucket": bucket, "key": key}
