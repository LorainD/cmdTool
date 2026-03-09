"""agent.intent — 用户意图解析"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ..core.config import AppConfig
from ..core.llm import LlmError, LlmMessage, api_key_present, chat_completion
from ..core.prompts import intent_prompt, system_prompt


@dataclass(frozen=True)
class Intent:
    action: str  # chat | migrate
    symbol: str
    raw: str
    llm_used: bool
    error: str | None = None

    @property
    def module(self) -> str:
        if "." in self.symbol:
            return self.symbol.split(".")[0]
        return self.symbol

    @property
    def func_name(self) -> str:
        if "." in self.symbol:
            return self.symbol.split(".", 1)[1]
        return self.symbol

    @property
    def search_terms(self) -> list[str]:
        if "." in self.symbol:
            return [self.func_name, self.module, self.symbol]
        return [self.symbol]


def _extract_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("{") and raw.endswith("}"):
        return json.loads(raw)
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(raw[start : end + 1])
    return json.loads(raw)


_DOT_IDENT = r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*"


def _extract_symbol_heuristic(user_text: str) -> str:
    m = re.search(
        r"(?:\b(?:symbol|operator|kernel|function)\b|算子|函数|符号)"
        r"\s*[:：]?\s*`?(" + _DOT_IDENT + r")`?",
        user_text, flags=re.IGNORECASE,
    )
    if m:
        return m.group(1)
    m = re.search(r"\b(ff_[A-Za-z0-9_.]+)\b", user_text)
    if m:
        return m.group(1)
    m = re.search(
        r"(?:迁移|移植|migrate|port)\s+(" + _DOT_IDENT + r")(?:\s|$|到|的|为|至)",
        user_text, flags=re.IGNORECASE,
    )
    if m:
        return m.group(1)
    m = re.search(
        r"(" + _DOT_IDENT + r")\s*(?:算子|函数|符号|\boperator\b|\bfunction\b|\bkernel\b)",
        user_text, flags=re.IGNORECASE,
    )
    if m:
        return m.group(1)
    t = user_text.strip()
    if re.fullmatch(_DOT_IDENT, t):
        if "_" in t or "." in t or len(t) >= 8:
            return t
    return ""


def _has_ffmpeg_context(user_text: str) -> bool:
    t = user_text.lower()
    return ("ffmpeg" in t) or ("libav" in t) or ("checkasm" in t)


def _looks_like_migrate(user_text: str) -> bool:
    t = user_text.strip().lower()
    keywords = ["迁移", "移植", "rvv", "riscv", "simd", "优化", "汇编",
                "checkasm", "编译", "生成", "patch", "migrate", "port"]
    return any(k in t for k in keywords)


def parse_intent(cfg: AppConfig, user_text: str) -> Intent:
    wants_migrate = _has_ffmpeg_context(user_text) and _looks_like_migrate(user_text)
    sym = _extract_symbol_heuristic(user_text)

    if sym and user_text.strip() == sym:
        return Intent(action="migrate", symbol=sym, raw="heuristic:symbol_only", llm_used=False)
    if wants_migrate and sym:
        return Intent(action="migrate", symbol=sym, raw="heuristic:migrate", llm_used=False)

    if api_key_present(cfg.llm):
        messages = [
            LlmMessage(role="system", content=system_prompt()),
            LlmMessage(role="user", content=intent_prompt(user_text)),
        ]
        try:
            raw = chat_completion(cfg.llm, messages, max_tokens=220)
            data = _extract_json(raw)
            action = str(data.get("action", "chat")).strip().lower()
            if action not in {"chat", "migrate"}:
                action = "chat"
            symbol = str(data.get("symbol", "")).strip() or sym
            if wants_migrate and action != "migrate":
                action = "migrate"
            return Intent(action=action, symbol=symbol, raw=raw, llm_used=True)
        except LlmError as e:
            action = "migrate" if wants_migrate else "chat"
            return Intent(action=action, symbol=sym, raw=str(e), llm_used=False, error=str(e))
        except Exception as e:
            action = "migrate" if wants_migrate else "chat"
            return Intent(action=action, symbol=sym, raw=repr(e), llm_used=False, error=repr(e))

    action = "migrate" if wants_migrate else "chat"
    return Intent(action=action, symbol=sym, raw="no_llm", llm_used=False)
