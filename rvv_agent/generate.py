from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .config import AppConfig
from .llm import LlmError, LlmMessage, chat_completion
from .prompts import generation_prompt, system_prompt
from .util import ensure_dir, write_json, write_text


@dataclass
class GenerationResult:
    package: dict
    raw_text: str
    llm_used: bool
    error: str | None = None


def _fallback_package(symbol: str) -> dict:
    return {
        "files": [
            {
                "path": f"libavcodec/riscv/{symbol}_rvv.S",
                "content": (
                    "/* TODO: auto-generated placeholder (LLM disabled). */\n"
                    ".text\n.align 2\n"
                    f".globl {symbol}\n.type {symbol}, @function\n"
                    f"{symbol}:\n\tret\n"
                ),
            }
        ],
        "patches": [],
    }


def generate_with_llm(cfg: AppConfig, symbol: str, analysis_json: dict) -> GenerationResult:
    messages = [
        LlmMessage(role="system", content=system_prompt()),
        LlmMessage(role="user", content=generation_prompt(symbol, json.dumps(analysis_json, ensure_ascii=False))),
    ]

    try:
        raw = chat_completion(cfg.llm, messages, max_tokens=2200)
        data = json.loads(raw)
        return GenerationResult(package=data, raw_text=raw, llm_used=True)
    except LlmError as e:
        fb = _fallback_package(symbol)
        return GenerationResult(package=fb, raw_text=str(e), llm_used=False, error=str(e))
    except Exception as e:
        fb = _fallback_package(symbol)
        return GenerationResult(package=fb, raw_text=repr(e), llm_used=False, error=repr(e))


def materialize_package(run_dir: Path, ffmpeg_root: Path, package: dict, *, apply: bool) -> list[Path]:
    out_paths: list[Path] = []

    artifacts_dir = run_dir / "artifacts"
    ensure_dir(artifacts_dir)
    write_json(artifacts_dir / "package.json", package)

    for f in package.get("files", []):
        rel = Path(str(f.get("path", "")))
        content = str(f.get("content", ""))
        if not rel.as_posix() or not content:
            continue

        # Always write a copy under runs/ for traceability.
        dst_trace = artifacts_dir / "files" / rel
        write_text(dst_trace, content)
        out_paths.append(dst_trace)

        if apply:
            dst = ffmpeg_root / rel
            ensure_dir(dst.parent)
            write_text(dst, content)
            out_paths.append(dst)

    # Save diff suggestions (do not auto-apply in MVP)
    for p in package.get("patches", []):
        p_path = artifacts_dir / "patches" / Path(str(p.get("path", "unknown"))).name
        diff = str(p.get("diff", ""))
        if diff:
            write_text(p_path.with_suffix(p_path.suffix + ".diff"), diff)
            out_paths.append(p_path.with_suffix(p_path.suffix + ".diff"))

    return out_paths
