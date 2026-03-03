from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .config import LlmConfig


@dataclass(frozen=True)
class LlmMessage:
    role: str  # system | user | assistant
    content: str


class LlmError(RuntimeError):
    pass


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def chat_completion(
    cfg: LlmConfig,
    messages: list[LlmMessage],
    *,
    max_tokens: int = 2048,
) -> str:
    api_key = os.getenv(cfg.api_key_env, "").strip()
    if not api_key:
        raise LlmError(f"Missing API key: env {cfg.api_key_env} is empty")

    url = cfg.base_url.rstrip("/") + "/chat/completions"

    payload: dict[str, Any] = {
        "model": cfg.model,
        "temperature": cfg.temperature,
        "max_tokens": max_tokens,
        "messages": [{"role": m.role, "content": m.content} for m in messages],
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=_headers(api_key),
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
        raise LlmError(f"HTTP {e.code} from LLM endpoint: {body[:2000]}") from e
    except Exception as e:
        raise LlmError(str(e)) from e

    try:
        data = json.loads(raw)
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        raise LlmError(f"Unexpected LLM response: {raw[:2000]}") from e
