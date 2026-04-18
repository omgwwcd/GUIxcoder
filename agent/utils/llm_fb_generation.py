import os
from openai import OpenAI
from dotenv import load_dotenv
load_dotenv()

_base = os.environ.get("OPENAILIKE_FB_BASE_URL") or os.environ.get("OPENAILIKE_BASE_URL")
_key = os.environ.get("OPENAILIKE_FB_API_KEY") or os.environ.get("OPENAILIKE_API_KEY")
_kwargs = dict(api_key=_key, base_url=_base)
if _base and ("localhost" in _base or "127.0.0.1" in _base):
    import httpx
    _kwargs["http_client"] = httpx.Client(proxy=None, trust_env=False)
client = OpenAI(**_kwargs) if _base and _key else None


def llm_fb_generation(messages, model, max_tokens=-1, max_completion_tokens=-1):
    if max_tokens > 0:
        chat_response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
        )
    elif max_completion_tokens > 0:
        chat_response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_completion_tokens=max_completion_tokens,
        )
    else:
        chat_response = client.chat.completions.create(
            model=model,
            messages=messages,
        )

    return chat_response.choices[0].message.content