from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .config import AppConfig
from .llm import LlmError, LlmMessage, chat_completion
from .prompts import build_fix_prompt, generation_prompt, system_prompt
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


def _extract_json(raw: str) -> dict:
    raw = raw.strip()
    # strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0]
        raw = raw.strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        return json.loads(raw[start : end + 1])
    return json.loads(raw)


def scan_existing_rvv_content(
    ffmpeg_root: Path,
    ref_files: list[str],
) -> dict[str, str]:
    """Read any .S files in the ref_files list that already exist in ffmpeg_root.

    Returns a dict mapping relative path -> existing content.
    This is passed to the generation prompt so the LLM knows to ADD, not replace.
    """
    existing: dict[str, str] = {}
    for rel in ref_files:
        if not rel.endswith(".S"):
            continue
        full = ffmpeg_root / rel
        if full.exists() and full.is_file():
            try:
                existing[rel] = full.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass
    # Also scan libavcodec/riscv/ for any .S files that look related
    riscv_dir = ffmpeg_root / "libavcodec" / "riscv"
    if riscv_dir.is_dir():
        for f in riscv_dir.glob("*.S"):
            rel = str(f.relative_to(ffmpeg_root))
            if rel not in existing:
                try:
                    existing[rel] = f.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    pass
    return existing


def generate_with_llm(
    cfg: AppConfig,
    symbol: str,
    analysis_json: dict,
    *,
    existing_files_map: dict[str, str] | None = None,
) -> GenerationResult:
    messages = [
        LlmMessage(role="system", content=system_prompt()),
        LlmMessage(
            role="user",
            content=generation_prompt(
                symbol,
                json.dumps(analysis_json, ensure_ascii=False),
                existing_files_map=existing_files_map,
            ),
        ),
    ]

    try:
        raw = chat_completion(cfg.llm, messages, max_tokens=2800)
        data = _extract_json(raw)
        return GenerationResult(package=data, raw_text=raw, llm_used=True)
    except LlmError as e:
        fb = _fallback_package(symbol)
        return GenerationResult(package=fb, raw_text=str(e), llm_used=False, error=str(e))
    except Exception as e:
        fb = _fallback_package(symbol)
        return GenerationResult(package=fb, raw_text=repr(e), llm_used=False, error=repr(e))


def fix_generation_with_llm(
    cfg: AppConfig,
    symbol: str,
    build_error: str,
    current_package: dict,
) -> GenerationResult:
    """Ask LLM to fix a package that failed to compile."""
    messages = [
        LlmMessage(role="system", content=system_prompt()),
        LlmMessage(
            role="user",
            content=build_fix_prompt(
                symbol,
                build_error,
                current_package.get("files", []),
            ),
        ),
    ]
    try:
        raw = chat_completion(cfg.llm, messages, max_tokens=2800)
        data = _extract_json(raw)
        return GenerationResult(package=data, raw_text=raw, llm_used=True)
    except Exception as e:
        return GenerationResult(
            package=current_package,
            raw_text=repr(e),
            llm_used=False,
            error=repr(e),
        )


def materialize_package(
    run_dir: Path,
    ffmpeg_root: Path,
    package: dict,
    *,
    apply: bool,
    attempt: int = 0,
) -> list[Path]:
    out_paths: list[Path] = []

    # Use a sub-directory per attempt so retries don't overwrite previous artifacts
    attempt_suffix = f"_attempt{attempt}" if attempt > 0 else ""
    artifacts_dir = run_dir / f"artifacts{attempt_suffix}"
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

    # Save diff suggestions
    for patch in package.get("patches", []):
        p_path = artifacts_dir / "patches" / Path(str(patch.get("path", "unknown"))).name
        diff = str(patch.get("diff", ""))
        if diff:
            write_text(p_path.with_suffix(p_path.suffix + ".diff"), diff)
            out_paths.append(p_path.with_suffix(p_path.suffix + ".diff"))

    return out_paths
