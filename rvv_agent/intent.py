from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .config import AppConfig
from .llm import LlmError, LlmMessage, chat_completion
from .prompts import intent_prompt, system_prompt


@dataclass(frozen=True)
class Intent:
    action: str
    symbol: str
    raw: str
    llm_used: bool
    error: str | None = None


def _extract_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("{") and raw.endswith("}"):
        return json.loads(raw)
    # Try to salvage: take first {...} block
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(raw[start : end + 1])
    return json.loads(raw)


def _fallback_symbol(user_text: str) -> str:
    # Prefer ff_* style
    m = re.search(r"\b(ff_[A-Za-z0-9_]+)\b", user_text)
    if m:
        return m.group(1)
    # Otherwise last token
    parts = [p for p in re.split(r"\s+", user_text.strip()) if p]
    return parts[-1] if parts else ""


def parse_intent(cfg: AppConfig, user_text: str) -> Intent:
    messages = [
        LlmMessage(role="system", content=system_prompt()),
        LlmMessage(role="user", content=intent_prompt(user_text)),
    ]

    try:
        raw = chat_completion(cfg.llm, messages, max_tokens=400)
        data = _extract_json(raw)
        action = str(data.get("action", "migrate"))
        symbol = str(data.get("symbol", "")).strip()
        if not symbol:
            symbol = _fallback_symbol(user_text)
        return Intent(action=action, symbol=symbol, raw=raw, llm_used=True)
    except LlmError as e:
        sym = _fallback_symbol(user_text)
        return Intent(action="migrate", symbol=sym, raw=str(e), llm_used=False, error=str(e))
    except Exception as e:
        sym = _fallback_symbol(user_text)
        return Intent(action="migrate", symbol=sym, raw=repr(e), llm_used=False, error=repr(e))
