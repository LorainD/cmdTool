from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .config import AppConfig
from .llm import LlmError, LlmMessage, api_key_present, chat_completion
from .prompts import intent_prompt, system_prompt


@dataclass(frozen=True)
class Intent:
    action: str  # chat | migrate
    symbol: str
    raw: str
    llm_used: bool
    error: str | None = None

    @property
    def module(self) -> str:
        """For dotted symbols like 'sbrdsp.neg_odd_64' returns 'sbrdsp'; otherwise same as symbol."""
        if "." in self.symbol:
            return self.symbol.split(".")[0]
        return self.symbol

    @property
    def func_name(self) -> str:
        """For dotted symbols like 'sbrdsp.neg_odd_64' returns 'neg_odd_64'; otherwise same as symbol."""
        if "." in self.symbol:
            return self.symbol.split(".", 1)[1]
        return self.symbol

    @property
    def search_terms(self) -> list[str]:
        """All terms worth searching for in the FFmpeg tree.
        For 'sbrdsp.neg_odd_64' returns ['neg_odd_64', 'sbrdsp', 'sbrdsp.neg_odd_64']
        so that both the C function and the module source file are found.
        """
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


def _extract_symbol_heuristic(user_text: str) -> str:
    # A "dotted C symbol": identifier segments separated by dots,
    # e.g. sbrdsp.neg_odd_64 or plain names like ff_vp8_idct16_add.
    _DOT_IDENT = r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*"

    # 1) Explicit keyword patterns: 算子/函数/符号/symbol/... followed by the name.
    m = re.search(
        r"(?:\b(?:symbol|operator|kernel|function)\b|算子|函数|符号)"
        r"\s*[:：]?\s*`?(" + _DOT_IDENT + r")`?",
        user_text,
        flags=re.IGNORECASE,
    )
    if m:
        return m.group(1)

    # 2) Common ff_* shortcut (may also have dots).
    m = re.search(r"\b(ff_[A-Za-z0-9_.]+)\b", user_text)
    if m:
        return m.group(1)

    # 3) Migrate-verb + dotted-name pattern: "迁移 sbrdsp.neg_odd_64 到/的/..."
    m = re.search(
        r"(?:迁移|移植|migrate|port)\s+(" + _DOT_IDENT + r")(?:\s|$|到|的|为|至)",
        user_text,
        flags=re.IGNORECASE,
    )
    if m:
        return m.group(1)

    # 3b) Reversed: "sbrdsp.neg_odd_64 算子" – dotted name followed by keyword.
    m = re.search(
        r"(" + _DOT_IDENT + r")\s*(?:算子|函数|符号|\boperator\b|\bfunction\b|\bkernel\b)",
        user_text,
        flags=re.IGNORECASE,
    )
    if m:
        return m.group(1)

    # 4) Single token – lone identifier / dotted-name.
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
    keywords = [
        "迁移",
        "移植",
        "rvv",
        "riscv",
        "simd",
        "优化",
        "汇编",
        "checkasm",
        "编译",
        "生成",
        "patch",
        "migrate",
        "port",
    ]
    return any(k in t for k in keywords)


def parse_intent(cfg: AppConfig, user_text: str) -> Intent:
    # Strong trigger: mentions ffmpeg context + migrate-ish keywords
    wants_migrate = _has_ffmpeg_context(user_text) and _looks_like_migrate(user_text)

    # Heuristic symbol extraction (works even without LLM)
    sym = _extract_symbol_heuristic(user_text)

    # If user typed a bare symbol, treat as migrate (convenience)
    if sym and user_text.strip() == sym:
        return Intent(action="migrate", symbol=sym, raw="heuristic:symbol_only", llm_used=False)

    # If clearly migrate and has symbol, don't waste LLM
    if wants_migrate and sym:
        return Intent(action="migrate", symbol=sym, raw="heuristic:migrate", llm_used=False)

    # Otherwise ask LLM to classify/extract (if available)
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

            # If heuristic says it's migrate with ffmpeg context, but LLM didn't, keep conservative migrate.
            if wants_migrate and action != "migrate":
                action = "migrate"

            return Intent(action=action, symbol=symbol, raw=raw, llm_used=True)
        except LlmError as e:
            action = "migrate" if wants_migrate else "chat"
            return Intent(action=action, symbol=sym, raw=str(e), llm_used=False, error=str(e))
        except Exception as e:
            action = "migrate" if wants_migrate else "chat"
            return Intent(action=action, symbol=sym, raw=repr(e), llm_used=False, error=repr(e))

    # No LLM configured: conservative
    action = "migrate" if wants_migrate else "chat"
    return Intent(action=action, symbol=sym, raw="no_llm", llm_used=False)
