from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Match:
    file: str
    line: int
    text: str


@dataclass(frozen=True)
class Discovery:
    symbol: str
    matches: list[Match]


def _iter_source_files(ffmpeg_root: Path) -> Iterable[Path]:
    ex_dirs = {".git", "build"}
    for path in ffmpeg_root.rglob("*"):
        if path.is_dir():
            continue
        if any(part in ex_dirs for part in path.parts):
            continue
        if path.suffix not in {".c", ".h", ".S", ".s", ".inc", ".cpp"}:
            continue
        yield path


def find_symbol_multi(
    ffmpeg_root: Path,
    terms: list[str],
    *,
    primary: str | None = None,
    max_matches: int = 400,
) -> Discovery:
    """Search for multiple terms and merge results (deduplicating by file+line).

    *primary* is the canonical symbol name stored on the returned Discovery
    (defaults to ``terms[0]``).
    """
    seen_lines: dict[str, set[int]] = {}  # rel_path -> set of line numbers added
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
            lines_seen = seen_lines.setdefault(rel, set())
            for i, line in enumerate(text.splitlines(), start=1):
                if i in lines_seen:
                    continue
                if token_re.search(line):
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

        for i, line in enumerate(text.splitlines(), start=1):
            if token_re.search(line):
                matches.append(
                    Match(
                        file=str(file.relative_to(ffmpeg_root)).replace("\\", "/"),
                        line=i,
                        text=line.strip(),
                    )
                )
                if len(matches) >= max_matches:
                    return Discovery(symbol=symbol, matches=matches)

    return Discovery(symbol=symbol, matches=matches)


def group_files(discovery: Discovery) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {
        "c_candidates": [],
        "x86_refs": [],
        "arm_refs": [],
        "aarch64_refs": [],
        "riscv_refs": [],
        "headers": [],
        "other": [],
    }

    seen: set[str] = set()
    for m in discovery.matches:
        f = m.file
        if f in seen:
            continue
        seen.add(f)

        if f.endswith(".h") or f.endswith(".inc"):
            groups["headers"].append(f)
            continue

        if "/x86/" in f:
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
    # Keep context small and deterministic.
    lines: list[str] = []
    for m in discovery.matches[:max_lines]:
        lines.append(f"{m.file}:{m.line}: {m.text}")
    return "\n".join(lines)
