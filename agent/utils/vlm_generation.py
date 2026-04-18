import os
import base64
import binascii
from dotenv import load_dotenv

from openai import OpenAI
try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None

load_dotenv()

_vlm_base = os.environ.get("OPENAILIKE_VLM_BASE_URL")
_vlm_key = os.environ.get("OPENAILIKE_VLM_API_KEY")
openai_client = None
if _vlm_base and _vlm_key:
    _vlm_kwargs = dict(api_key=_vlm_key, base_url=_vlm_base)
    if "localhost" in _vlm_base or "127.0.0.1" in _vlm_base:
        import httpx
        _vlm_kwargs["http_client"] = httpx.Client(proxy=None, trust_env=False)
    openai_client = OpenAI(**_vlm_kwargs)

anthropic_client = None
if Anthropic and os.environ.get("ANTHROPIC_VLM_API_KEY"):
    anthropic_client = Anthropic(
        api_key=os.environ["ANTHROPIC_VLM_API_KEY"],
        base_url=os.environ.get("ANTHROPIC_VLM_BASE_URL"),
    )

# ── Helper: light-weight MIME sniffing ─────────────────────────────────────────
def _detect_media_type(b64: str, fallback: str = "image/jpeg") -> str:
    """
    Inspect the first bytes of a Base-64 image and deduce its MIME type.
    Defaults to `fallback` if unknown.
    """
    try:
        hdr = base64.b64decode(b64[:64], validate=False)  # only a few bytes
    except binascii.Error:
        return fallback

    if hdr.startswith(b"\xFF\xD8\xFF"):               # JPEG SOI
        return "image/jpeg"
    if hdr.startswith(b"\x89PNG\r\n\x1A\n"):          # PNG signature
        return "image/png"
    if hdr.startswith(b"GIF87a") or hdr.startswith(b"GIF89a"):
        return "image/gif"
    if hdr[0:4] == b"RIFF" and hdr[8:12] == b"WEBP":  # WebP
        return "image/webp"
    return fallback

# ── Helper: OpenAI → Anthropic message conversion ─────────────────────────────
def _convert_to_anthropic(messages: list[dict]) -> tuple[str | None, list[dict]]:
    """
    Transform OpenAI-style messages (including image_url blocks)
    into Anthropic v1 format.
    """
    converted: list[dict] = []
    system_text: str | None = None

    for msg in messages:
        role = msg["role"]

        # System prompt is passed separately.
        if role == "system":
            system_text = msg["content"]
            continue

        new_msg: dict = {"role": role, "content": []}

        # 1️⃣ Pure-text message
        if isinstance(msg["content"], str):
            new_msg["content"].append({"type": "text", "text": msg["content"]})
            converted.append(new_msg)
            continue

        # 2️⃣ Mixed content list (text + images)
        for block in msg["content"]:
            if block["type"] == "text":
                new_msg["content"].append({"type": "text", "text": block["text"]})

            elif block["type"] == "image_url":
                url = block["image_url"]["url"]        # data:image/xyz;base64,…
                media_prefix, b64_data = url.split(";base64,", maxsplit=1)

                # Prefer real signature over the prefix (fixes 400 error)
                media_type = _detect_media_type(b64_data, fallback=media_prefix.replace("data:", ""))

                new_msg["content"].append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64_data,
                        },
                    }
                )

        converted.append(new_msg)

    return system_text, converted

# ── Unified generation function ────────────────────────────────────────────────
def vlm_generation(messages: list[dict], model: str, **kwargs) -> str:
    """
    Route the request to Anthropic or OpenAI depending on `model`.
    Includes retry logic for rate limit (429) errors.
    """
    import time

    is_anthropic = any(tag in model.lower() for tag in ("anthropic", "claude"))
    max_retries = 3
    retry_delay = 5  # seconds

    for attempt in range(max_retries):
        try:
            if is_anthropic:
                system_text, claude_msgs = _convert_to_anthropic(messages)

                response = anthropic_client.messages.create(
                    model=model,
                    system=system_text,
                    messages=claude_msgs,
                    max_tokens=kwargs.pop("max_tokens", 4096),   # Anthropic requires it
                    **kwargs,
                )
                # Claude replies with a list of content blocks; first is always text.
                return response.content[0].text

            # ── OpenAI-compatible call ────────────────────────────────────────────────
            chat_response = openai_client.chat.completions.create(
                model=model,
                messages=messages,
                **kwargs,
            )
            msg = chat_response.choices[0].message
            # 兼容 thinking 模式（如 qwen3-vl）：回答可能在 reasoning 字段
            content = msg.content or ""
            if not content and hasattr(msg, "reasoning") and msg.reasoning:
                content = msg.reasoning
            return content

        except Exception as e:
            error_str = str(e)
            # Check for rate limit error
            if "429" in error_str or "rate limit" in error_str.lower():
                if attempt < max_retries - 1:
                    print(f"[VLM] Rate limit hit, retrying in {retry_delay}s... ({attempt + 1}/{max_retries})")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                    continue
            # Re-raise other errors
            raise

    # Should not reach here, but return empty if all retries fail
    return ""

if __name__ == "__main__":
    # Example usage (for testing purposes)
    example_messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "What is the capital of France?"},
    ]
    print(vlm_generation(example_messages, model="claude-3-7-sonnet-20250219"))