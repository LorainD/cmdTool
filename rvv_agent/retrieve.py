from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .config import AppConfig
from .intent import Intent
from .llm import LlmError, LlmMessage, chat_completion
from .prompts import retrieval_prompt, system_prompt
from .search import Discovery, find_symbol, find_symbol_multi, group_files


@dataclass
class RetrievalResult:
    discovery: Discovery
    selected: dict
    raw_text: str
    llm_used: bool
    error: str | None = None
    existing_rvv: list[str] = field(default_factory=list)  # RVV files already in the repo


def _extract_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("{") and raw.endswith("}"):
        return json.loads(raw)
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(raw[start : end + 1])
    return json.loads(raw)


def _fallback_selection(discovery: Discovery) -> dict:
    g = group_files(discovery)
    # Always include riscv Makefile if exists (helps patch)
    makefiles = []
    if any(m.file.startswith("libavcodec/riscv/") for m in discovery.matches):
        makefiles.append("libavcodec/riscv/Makefile")
    else:
        makefiles.append("libavcodec/riscv/Makefile")

    return {
        "symbol": discovery.symbol,
        "c": g["c_candidates"][:5],
        "x86": g["x86_refs"][:5],
        "arm": g["arm_refs"][:5] + g["aarch64_refs"][:5],
        "riscv": g["riscv_refs"][:5],
        "headers": g["headers"][:5],
        "makefiles": makefiles,
        "checkasm": ["tests/checkasm/checkasm.c"],
        "notes": "LLM 未运行或解析失败，使用 fallback 选择。",
    }


def _scan_existing_rvv(ffmpeg_root: Path, module: str) -> list[str]:
    """Scan libavcodec/riscv/ for files whose name contains the module name.
    Returns relative paths (relative to ffmpeg_root).
    """
    riscv_dir = ffmpeg_root / "libavcodec" / "riscv"
    if not riscv_dir.exists():
        return []
    module_lower = module.lower()
    found: list[str] = []
    for f in sorted(riscv_dir.iterdir()):
        if f.is_file() and module_lower in f.name.lower():
            found.append(str(f.relative_to(ffmpeg_root)).replace("\\", "/"))
    return found


def select_references(cfg: AppConfig, ffmpeg_root: Path, intent_or_symbol: "Intent | str") -> RetrievalResult:
    if isinstance(intent_or_symbol, str):
        symbol = intent_or_symbol
        module = intent_or_symbol
        terms = [symbol]
    else:
        symbol = intent_or_symbol.symbol
        module = intent_or_symbol.module
        terms = intent_or_symbol.search_terms

    # Check for existing RVV implementations FIRST, before full text search.
    existing_rvv = _scan_existing_rvv(ffmpeg_root, module)

    # Multi-term search: for 'sbrdsp.neg_odd_64' this searches func_name ('neg_odd_64'),
    # module ('sbrdsp'), and the dotted form – so sbrdsp.c + neg_odd_64 hits are all found
    # even though the dotted literal never appears verbatim in source code.
    if len(terms) > 1:
        discovery = find_symbol_multi(ffmpeg_root, terms, primary=symbol)
    else:
        discovery = find_symbol(ffmpeg_root, symbol)
    grouped = group_files(discovery)

    messages = [
        LlmMessage(role="system", content=system_prompt()),
        LlmMessage(role="user", content=retrieval_prompt(symbol, grouped, discovery.matches[:120])),
    ]

    try:
        raw = chat_completion(cfg.llm, messages, max_tokens=900)
        data = _extract_json(raw)
        if not isinstance(data, dict):
            raise ValueError("retrieval json is not object")
        return RetrievalResult(discovery=discovery, selected=data, raw_text=raw,
                               llm_used=True, existing_rvv=existing_rvv)
    except LlmError as e:
        fb = _fallback_selection(discovery)
        return RetrievalResult(discovery=discovery, selected=fb, raw_text=str(e),
                               llm_used=False, error=str(e), existing_rvv=existing_rvv)
    except Exception as e:
        fb = _fallback_selection(discovery)
        return RetrievalResult(discovery=discovery, selected=fb, raw_text=repr(e),
                               llm_used=False, error=repr(e), existing_rvv=existing_rvv)
