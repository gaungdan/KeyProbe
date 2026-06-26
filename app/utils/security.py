"""Security helpers."""


def mask_api_key(api_key: str) -> str:
    """Mask an API key so logs and previews never expose the full value."""

    key = api_key.strip()
    if not key:
        return ""
    if len(key) <= 8:
        return f"{key[:2]}***"
    return f"{key[:6]}********{key[-4:]}"

