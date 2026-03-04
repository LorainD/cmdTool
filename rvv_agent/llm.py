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


def api_key_present(cfg: LlmConfig) -> bool:
    return bool(os.getenv(cfg.api_key_env, "").strip())


def llm_status(cfg: LlmConfig) -> dict[str, object]:
    return {
        "endpoint_url": cfg.base_url,
        "model": cfg.model,
        "api_key_env": cfg.api_key_env,
        "api_key_present": api_key_present(cfg),
        "temperature": cfg.temperature,
    }


def _endpoint_url(cfg: LlmConfig) -> str:
    url = (cfg.base_url or "").strip().rstrip("/")
    if not url:
        raise LlmError("Missing LLM endpoint URL: set llm.base_url in rvv_agent.toml")
    # Auto-append the chat completions path when only a base URL is given,
    # e.g. "https://api.openai.com/v1"  →  ".../v1/chat/completions"
    if not url.endswith("/chat/completions"):
        url = url + "/chat/completions"
    return url


def chat_completion(
    cfg: LlmConfig,
    messages: list[LlmMessage],
    *,
    max_tokens: int = 2048,
    timeout_seconds: float = 120.0,
) -> str:
    api_key = os.getenv(cfg.api_key_env, "").strip()
    if not api_key:
        raise LlmError(f"Missing API key: env {cfg.api_key_env} is empty")

    url = _endpoint_url(cfg)

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
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            body = ""
        raise LlmError(f"HTTP {e.code} from {url}: {body[:2000]}") from e
    except Exception as e:
        raise LlmError(f"LLM request failed at {url}: {e}") from e

    try:
        data = json.loads(raw)
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        raise LlmError(f"Unexpected LLM response: {raw[:2000]}") from e


def probe_llm(cfg: LlmConfig) -> dict[str, object]:
    status = llm_status(cfg)
    try:
        status["endpoint_url_normalized"] = _endpoint_url(cfg)
    except Exception as e:
        status["probe_ok"] = False
        status["probe_error"] = str(e)
        return status

    probe_timeout = float(os.getenv("RVV_AGENT_LLM_PROBE_TIMEOUT", "10"))

    try:
        text = chat_completion(
            cfg,
            [
                LlmMessage(role="system", content="You are a helpful assistant."),
                LlmMessage(role="user", content="Reply with: OK"),
            ],
            max_tokens=8,
            timeout_seconds=probe_timeout,
        )
        status["probe_ok"] = True
        status["probe_reply"] = text.strip()[:200]
        return status
    except Exception as e:
        status["probe_ok"] = False
        status["probe_error"] = str(e)
        return status
