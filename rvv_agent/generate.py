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
    symbol: str = "",
) -> dict[str, str]:
    """Read existing files that the LLM needs to do full-content merge.

    Only reads:
    - Files explicitly listed in ref_files (filtered to init.c / Makefile)
    - Makefile in libavcodec/riscv/
    - *init*.c files in libavcodec/riscv/ whose name matches the module

    .S files are intentionally NOT included here: the pipeline appends new
    functions to .S files programmatically (in materialize_package), so the
    LLM only needs to output the *new* functions, not the full merged content.
    """
    existing: dict[str, str] = {}

    def _read(rel: str) -> None:
        full = ffmpeg_root / rel
        if full.exists() and full.is_file():
            try:
                existing[rel] = full.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass

    # Extract module name from symbol (e.g. "sbrdsp" from "sbrdsp.neg_odd_64")
    module_lower = symbol.split(".")[0].lower() if symbol else ""

    # From ref_files: only include init.c and Makefile (NOT .S files – those are appended)
    for rel in ref_files:
        p = Path(rel)
        if p.suffix == ".S":
            continue  # .S files handled by append logic in materialize_package
        _read(rel)

    # Scan libavcodec/riscv/ for Makefile and module-matched *init*.c
    riscv_dir = ffmpeg_root / "libavcodec" / "riscv"
    if riscv_dir.is_dir():
        for f in riscv_dir.iterdir():
            if not f.is_file():
                continue
            rel = str(f.relative_to(ffmpeg_root)).replace("\\", "/")
            if rel in existing:
                continue
            if f.name == "Makefile":
                _read(rel)
            elif "init" in f.name and f.suffix == ".c":
                # Only read init.c files related to the current module
                if not module_lower or module_lower in f.name.lower():
                    _read(rel)
            # .S files: deliberately excluded to avoid LLM accidentally overwriting

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
        raw = chat_completion(cfg.llm, messages, max_tokens=2800, stage="generate")
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
        raw = chat_completion(cfg.llm, messages, max_tokens=2800, stage="fix")
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

            # ── .S files: APPEND new functions instead of overwriting ─────────
            # The LLM is instructed to output only the *new* functions.
            # We append them to the end of any existing file so previously
            # implemented RVV functions are never lost.
            if rel.suffix == ".S" and dst.exists():
                existing_content = dst.read_text(encoding="utf-8", errors="replace")
                # Avoid duplicate: skip if this exact content is already present
                if content.strip() and content.strip() not in existing_content:
                    merged = existing_content.rstrip("\n") + "\n\n" + content.lstrip("\n")
                    write_text(dst, merged)
                    out_paths.append(dst)
                # else: already applied, nothing to do
            else:
                write_text(dst, content)
                out_paths.append(dst)

    # Save diff suggestions and apply with `patch` if apply=True
    for patch in package.get("patches", []):
        rel_patch = str(patch.get("path", ""))
        diff = str(patch.get("diff", ""))
        if not rel_patch or not diff:
            continue
        p_name = Path(rel_patch).name
        p_path = artifacts_dir / "patches" / p_name
        write_text(p_path.with_suffix(p_path.suffix + ".diff"), diff)
        out_paths.append(p_path.with_suffix(p_path.suffix + ".diff"))

        if apply:
            import subprocess
            target = ffmpeg_root / rel_patch
            ensure_dir(target.parent)
            # .resolve() converts the relative path to absolute so that
            # subprocess.run(cwd=ffmpeg_root) can still find the diff file.
            diff_file = p_path.with_suffix(p_path.suffix + ".diff").resolve()
            result = subprocess.run(
                ["patch", "-p1", "--forward", "--reject-file=-",
                 "-i", str(diff_file)],
                cwd=str(ffmpeg_root),
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                out_paths.append(target)
            else:
                write_text(
                    artifacts_dir / "patches" / (p_name + ".patch_error.txt"),
                    result.stdout + result.stderr,
                )

    return out_paths
