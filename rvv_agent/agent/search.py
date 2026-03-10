"""agent.search — 搜索/检索 Agent

负责：
  - ffmpeg 源树 symbol 定位（Match / Discovery）
  - LLM 辅助筛选参考文件（RetrievalResult / select_references）
  - 从文件片段构建 LLM 上下文（build_context_from_files）
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from ..core.config import AppConfig
from ..core.llm import LlmError, LlmMessage, chat_completion
from ..core.prompts import retrieval_prompt, system_prompt

if TYPE_CHECKING:
    from .chat import Intent

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Match:
    file: str
    line: int
    text: str


@dataclass(frozen=True)
class Discovery:
    symbol: str
    matches: list[Match]

# ---------------------------------------------------------------------------
# Source file iteration
# ---------------------------------------------------------------------------

def _iter_source_files(ffmpeg_root: Path) -> Iterable[Path]:
    ex_dirs = {".git", "build"}
    for path in ffmpeg_root.rglob("*"):
        if path.is_dir():
            continue
        if any(part in ex_dirs for part in path.parts):
            continue
        if path.suffix not in {".c", ".h", ".S", ".s", ".inc", ".cpp", ".asm"}:
            continue
        yield path

# ---------------------------------------------------------------------------
# Symbol search
# ---------------------------------------------------------------------------

def find_symbol_multi(
    ffmpeg_root: Path,
    terms: list[str],
    *,
    primary: str | None = None,
    max_matches: int = 400,
) -> Discovery:
    """多关键词搜索并合并（按 file+line 去重）。"""
    seen_lines: dict[str, set[int]] = {}
    all_matches: list[Match] = []
    for term in terms:
        token_re = re.compile(r"\b" + re.escape(term) + r"\b")
        for file in _iter_source_files(ffmpeg_root):
            rel = str(file.relative_to(ffmpeg_root)).replace("\\", "/")
            try:
                text = file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if term not in text:
                continue
            is_asm_file = file.suffix in {".S", ".s", ".asm"}
            lines_seen = seen_lines.setdefault(rel, set())
            for i, line in enumerate(text.splitlines(), start=1):
                if i in lines_seen:
                    continue
                if (is_asm_file and term in line) or (not is_asm_file and token_re.search(line)):
                    lines_seen.add(i)
                    all_matches.append(Match(file=rel, line=i, text=line.strip()))
                    if len(all_matches) >= max_matches:
                        return Discovery(symbol=primary or terms[0], matches=all_matches)
    return Discovery(symbol=primary or terms[0], matches=all_matches)


def find_symbol(ffmpeg_root: Path, symbol: str, *, max_matches: int = 400) -> Discovery:
    token_re = re.compile(r"\b" + re.escape(symbol) + r"\b")
    matches: list[Match] = []
    for file in _iter_source_files(ffmpeg_root):
        try:
            text = file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if symbol not in text:
            continue
        is_asm_file = file.suffix in {".S", ".s", ".asm"}
        for i, line in enumerate(text.splitlines(), start=1):
            if (is_asm_file and symbol in line) or (not is_asm_file and token_re.search(line)):
                matches.append(Match(
                    file=str(file.relative_to(ffmpeg_root)).replace("\\", "/"),
                    line=i,
                    text=line.strip(),
                ))
                if len(matches) >= max_matches:
                    return Discovery(symbol=symbol, matches=matches)
    return Discovery(symbol=symbol, matches=matches)


def group_files(discovery: Discovery) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {
        "c_candidates": [], "x86_refs": [], "arm_refs": [],
        "aarch64_refs": [], "riscv_refs": [], "headers": [], "other": [],
    }
    seen: set[str] = set()
    for m in discovery.matches:
        f = m.file
        if f in seen:
            continue
        seen.add(f)
        if f.endswith(".h") or f.endswith(".inc"):
            groups["headers"].append(f)
        elif "/x86/" in f:
            groups["x86_refs"].append(f)
        elif "/arm/" in f:
            groups["arm_refs"].append(f)
        elif "/aarch64/" in f:
            groups["aarch64_refs"].append(f)
        elif "/riscv/" in f:
            groups["riscv_refs"].append(f)
        elif f.endswith(".c") or f.endswith(".cpp"):
            groups["c_candidates"].append(f)
        else:
            groups["other"].append(f)
    return groups


def build_llm_context(discovery: Discovery, *, max_lines: int = 160) -> str:
    lines: list[str] = []
    for m in discovery.matches[:max_lines]:
        lines.append(f"{m.file}:{m.line}: {m.text}")
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Context builder from files
# ---------------------------------------------------------------------------

def build_context_from_files(
    ffmpeg_root: Path,
    *,
    symbol: str,
    files: list[str],
    max_total_chars: int = 20000,
    window: int = 3,
) -> str:
    token_re = re.compile(r"\b" + re.escape(symbol) + r"\b")
    chunks: list[str] = []
    total = 0
    for rel in files:
        p = ffmpeg_root / rel
        if not p.exists() or not p.is_file():
            continue
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        is_asm_file = p.suffix in {".S", ".s", ".asm"}
        hits: list[int] = (
            [i for i, line in enumerate(lines) if symbol in line]
            if is_asm_file
            else [i for i, line in enumerate(lines) if token_re.search(line)]
        )
        if not hits:
            snippet = "\n".join(lines[: min(40, len(lines))])
            block = f"--- {rel} (head) ---\n{snippet}\n"
            chunks.append(block)
            total += len(block)
        else:
            for i in hits[:6]:
                start = max(0, i - window)
                end = min(len(lines), i + window + 1)
                snippet = "\n".join(f"{j+1:6d}: {lines[j]}" for j in range(start, end))
                block = f"--- {rel} (around {symbol} @ line {i+1}) ---\n{snippet}\n"
                chunks.append(block)
                total += len(block)
                if total >= max_total_chars:
                    break
        if total >= max_total_chars:
            break
    return "\n".join(chunks)[:max_total_chars]

# ---------------------------------------------------------------------------
# Retrieval result + LLM-assisted reference selection
# ---------------------------------------------------------------------------

@dataclass
class RetrievalResult:
    discovery: Discovery
    selected: dict
    raw_text: str
    llm_used: bool
    error: str | None = None
    existing_rvv: list[str] = field(default_factory=list)


def _extract_retrieval_json(raw: str) -> dict:
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
    return {
        "symbol": discovery.symbol,
        "c": g["c_candidates"][:5],
        "x86": g["x86_refs"][:5],
        "arm": g["arm_refs"][:5] + g["aarch64_refs"][:5],
        "riscv": g["riscv_refs"][:5],
        "headers": g["headers"][:5],
        "makefiles": ["libavcodec/riscv/Makefile"],
        "checkasm": ["tests/checkasm/checkasm.c"],
        "notes": "LLM 未运行或解析失败，使用 fallback 选择。",
    }


def _scan_existing_rvv(ffmpeg_root: Path, module: str) -> list[str]:
    riscv_dir = ffmpeg_root / "libavcodec" / "riscv"
    if not riscv_dir.exists():
        return []
    module_lower = module.lower()
    found: list[str] = []
    for f in sorted(riscv_dir.iterdir()):
        if f.is_file() and module_lower in f.name.lower():
            found.append(str(f.relative_to(ffmpeg_root)).replace("\\", "/"))
    return found


def select_references(
    cfg: AppConfig,
    ffmpeg_root: Path,
    intent_or_symbol: "Intent | str",
) -> RetrievalResult:
    """LLM 辅助筛选参考文件。"""
    if isinstance(intent_or_symbol, str):
        symbol = intent_or_symbol
        module = intent_or_symbol
        terms = [symbol]
    else:
        symbol = intent_or_symbol.symbol
        module = intent_or_symbol.module
        terms = intent_or_symbol.search_terms

    existing_rvv = _scan_existing_rvv(ffmpeg_root, module)
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
        raw = chat_completion(cfg.llm, messages, max_tokens=900, stage="retrieve")
        data = _extract_retrieval_json(raw)
        if not isinstance(data, dict):
            raise ValueError("retrieval json is not dict")
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


# ---------------------------------------------------------------------------
# Context-aware stage wrapper
# ---------------------------------------------------------------------------

def search(ctx: "MigrationContext") -> "MigrationContext":
    """Context-aware search stage.

    Runs :func:`find_symbol` on ``ctx.operator`` / ``ctx.repo_root`` and
    stores results back into *ctx*.

    Updates
    -------
    ``ctx.discovery``   — full :class:`Discovery` result.
    ``ctx.source_file`` — primary source file path (first match, if any).
    """
    disc = find_symbol(ctx.repo_root, ctx.operator)
    ctx.discovery = disc
    if disc.matches:
        ctx.source_file = disc.matches[0].file
    return ctx
