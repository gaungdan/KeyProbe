"""AWS Signature Version 4 support for direct Bedrock Runtime calls."""

import hashlib
import hmac
from datetime import UTC, datetime
from urllib.parse import quote, urlparse


def parse_aws_credentials(value: str) -> tuple[str, str, str | None] | None:
    """Parse ``accessKeyId|secretAccessKey[|sessionToken]`` credentials."""

    parts = [part.strip() for part in value.split("|")]
    if len(parts) not in {2, 3} or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1], parts[2] if len(parts) == 3 and parts[2] else None


def sign_bedrock_request(
    *,
    credentials: tuple[str, str, str | None],
    method: str,
    url: str,
    payload: bytes = b"",
    now: datetime | None = None,
) -> dict[str, str]:
    """Return SigV4 headers for an AWS Bedrock control/runtime request."""

    access_key, secret_key, session_token = credentials
    parsed = urlparse(url)
    host = parsed.netloc
    region, service = _region_and_service(host)
    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    amz_date = timestamp.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = timestamp.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(payload).hexdigest()
    canonical_uri = quote(parsed.path or "/", safe="/-_.~%")
    canonical_query = parsed.query
    headers = {
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    if session_token:
        headers["x-amz-security-token"] = session_token
    signed_headers = ";".join(sorted(headers))
    canonical_headers = "".join(f"{name}:{headers[name]}\n" for name in sorted(headers))
    canonical_request = "\n".join(
        [method.upper(), canonical_uri, canonical_query, canonical_headers, signed_headers, payload_hash]
    )
    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    signing_key = _signing_key(secret_key, date_stamp, region, service)
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    headers["authorization"] = (
        "AWS4-HMAC-SHA256 "
        f"Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return {name.title() if name != "authorization" else "Authorization": value for name, value in headers.items()}


def _region_and_service(host: str) -> tuple[str, str]:
    parts = host.split(".")
    service = "bedrock-runtime" if parts and parts[0].startswith("bedrock-runtime") else "bedrock"
    for part in parts:
        if part.startswith(("us-", "eu-", "ap-", "sa-", "ca-", "me-", "af-", "il-")):
            return part, service
    raise ValueError("无法从 Bedrock 地址识别 AWS 区域")


def _signing_key(secret_key: str, date: str, region: str, service: str) -> bytes:
    key = ("AWS4" + secret_key).encode("utf-8")
    for value in (date, region, service, "aws4_request"):
        key = hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()
    return key
