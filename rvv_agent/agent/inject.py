"""agent.inject — Injection Agent

Reads run_dir/generate/plan.json produced by the Generator LLM and
applies the code changes to the FFmpeg workspace in a non-destructive way:
  - "create"  : write a brand-new file
  - "append"  : for .S files, append at end; for init.c / Makefile, call the
                Locator LLM to find the right insertion line, then insert.

Debug artifacts written to run_dir/apply[_fixN]/:
    log.json       -- [{target_path, action, applied_at, success, error}]
    snapshot/      -- copy of every modified file after injection
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ..core.util import ensure_dir, write_json, write_text

if TYPE_CHECKING:
    from ..core.config import AppConfig


@dataclass
class InjectionLogEntry:
    target_path: str
    action: str
    applied_at: str = ""
    success: bool = True
    error: str = ""


@dataclass
class InjectResult:
    logs: list = field(default_factory=list)
    applied_paths: list = field(default_factory=list)
    apply_dir: Path = field(default_factory=lambda: Path("."))


def _locate_insertion_line(cfg, target_path, existing_content, snippet, anchor_hint):
    """Call Locator LLM to find 0-based line to insert after. Returns -1 for end."""
    from ..core.llm import LlmError, LlmMessage, chat_completion
    from ..core.prompts import injection_locator_prompt, system_prompt

    messages = [
        LlmMessage(role="system", content=system_prompt()),
        LlmMessage(role="user", content=injection_locator_prompt(
            target_path=target_path,
            existing_content=existing_content,
            snippet=snippet,
            anchor_hint=anchor_hint,
        )),
    ]
    try:
        raw = chat_completion(cfg.llm, messages, max_tokens=300, stage="inject_locate")
        raw = raw.strip()
        s = raw.find("{"); e = raw.rfind("}")
        data = json.loads(raw[s:e+1]) if s != -1 and e > s else json.loads(raw)
        if data.get("strategy") == "insert_after_line":
            return int(data.get("line", -1))
        return -1
    except Exception:
        return -1


def _insert_after_line(existing, snippet, after_line):
    lines = existing.splitlines(keepends=True)
    pos = min(after_line + 1, len(lines))
    inject = snippet.lstrip("\n")
    if not inject.endswith("\n"):
        inject += "\n"
    lines.insert(pos, inject)
    return "".join(lines)


def _snippet_already_present(existing, snippet):
    for line in snippet.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith(("//", "/*", "#", ";")):
            return stripped in existing
    return False


def _snapshot(apply_dir, src_file):
    try:
        snap_dir = apply_dir / "snapshot"
        parts = src_file.parts
        rel = Path(*parts[-3:]) if len(parts) >= 3 else Path(src_file.name)
        snap = snap_dir / rel
        ensure_dir(snap.parent)
        write_text(snap, src_file.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        pass


def _inject_asm_file(dst, content, apply_dir):
    from pathlib import Path as _Path
    rel = str(dst)
    if not dst.exists():
        ensure_dir(dst.parent)
        write_text(dst, content)
        _snapshot(apply_dir, dst)
        return InjectionLogEntry(target_path=rel, action="create", applied_at="new_file")
    existing = dst.read_text(encoding="utf-8", errors="replace")
    if _snippet_already_present(existing, content):
        return InjectionLogEntry(target_path=rel, action="append", applied_at="skipped_duplicate")
    merged = existing.rstrip("\n") + "\n\n" + content.lstrip("\n")
    write_text(dst, merged)
    _snapshot(apply_dir, dst)
    return InjectionLogEntry(target_path=rel, action="append", applied_at="end")


def _inject_text_file(dst, content, anchor_hint, apply_dir, cfg):
    rel = str(dst)
    if not dst.exists():
        ensure_dir(dst.parent)
        write_text(dst, content)
        _snapshot(apply_dir, dst)
        return InjectionLogEntry(target_path=rel, action="create", applied_at="new_file")
    existing = dst.read_text(encoding="utf-8", errors="replace")
    if _snippet_already_present(existing, content):
        return InjectionLogEntry(target_path=rel, action="append", applied_at="skipped_duplicate")
    insert_after = -1
    applied_at = "end"
    if cfg is not None and anchor_hint:
        try:
            insert_after = _locate_insertion_line(cfg, rel, existing, content, anchor_hint)
        except Exception:
            insert_after = -1
    if insert_after >= 0:
        new_content = _insert_after_line(existing, content, insert_after)
        applied_at = f"line:{insert_after}"
    else:
        new_content = existing.rstrip("\n") + "\n" + content.lstrip("\n")
    write_text(dst, new_content)
    _snapshot(apply_dir, dst)
    return InjectionLogEntry(target_path=rel, action="append", applied_at=applied_at)


def inject_generate_plan(
    run_dir: Path,
    ffmpeg_root: Path,
    generate_plan: dict,
    *,
    apply: bool,
    attempt: int = 0,
    cfg=None,
) -> InjectResult:
    """Apply generate_plan to ffmpeg_root.

    Parameters
    ----------
    run_dir:       Current run directory (for apply log + snapshot).
    ffmpeg_root:   Root of FFmpeg source tree.
    generate_plan: {generated:[{target_path, action, content, anchor_hint}]} dict.
    apply:         If False, perform a dry-run (no file writes).
    attempt:       Fix-loop attempt index (0 = initial generation).
    cfg:           AppConfig for Locator LLM calls; None = always append-at-end.

    Directory layout written:
        run_dir/apply[_fixN]/
            log.json        -- per-file injection log
            snapshot/       -- copies of modified files
    """
    suffix = f"_fix{attempt}" if attempt > 0 else ""
    apply_dir = run_dir / f"apply{suffix}"
    ensure_dir(apply_dir)
    result = InjectResult(apply_dir=apply_dir)

    for item in generate_plan.get("generated", []):
        target_path = str(item.get("target_path", "")).strip()
        action = str(item.get("action", "create")).strip()
        content = str(item.get("content", ""))
        anchor_hint = str(item.get("anchor_hint", ""))
        if not target_path:
            continue
        # Safety: only "create" and "append" are permitted; block any deletion action
        if action.lower() in ("delete", "replace", "remove", "overwrite"):
            result.logs.append(InjectionLogEntry(
                target_path=target_path, action=action, success=False,
                error=f"action '{action}' blocked: only 'create'/'append' permitted",
            ))
            continue
        if not content:
            continue
        dst = ffmpeg_root / target_path
        try:
            if not apply:
                log = InjectionLogEntry(target_path=target_path, action=action, applied_at="dry_run")
            elif Path(target_path).suffix == ".S":
                log = _inject_asm_file(dst, content, apply_dir)
            else:
                log = _inject_text_file(dst, content, anchor_hint, apply_dir, cfg)
        except Exception as exc:
            log = InjectionLogEntry(target_path=target_path, action=action, success=False, error=str(exc))

        result.logs.append(log)
        if log.success and log.applied_at not in ("dry_run", "skipped_duplicate"):
            result.applied_paths.append(dst)

    # Record inject summary to trajectory
    try:
        from ..core.llm import record_trajectory_action  # local to avoid circular
        inject_summary = ", ".join(
            f"{e.target_path}@{e.applied_at}"
            for e in result.logs
            if e.success and e.applied_at not in ("dry_run", "skipped_duplicate")
        )
        record_trajectory_action(
            "inject",
            f"Injected {len(result.applied_paths)} file(s) (apply={apply}, attempt={attempt})",
            detail=inject_summary or "(none)",
        )
    except Exception:
        pass

    write_json(apply_dir / "log.json", [
        {"target_path": e.target_path, "action": e.action,
         "applied_at": e.applied_at, "success": e.success, "error": e.error}
        for e in result.logs
    ])
    return result


# ---------------------------------------------------------------------------
# Context-aware stage wrapper
# ---------------------------------------------------------------------------

def insert(ctx: "MigrationContext", *, attempt: int = 0) -> "MigrationContext":
    """Context-aware injection stage.

    Reads ``ctx.current_gen`` and applies the generate plan to the FFmpeg
    workspace.  Respects ``ctx.apply`` and writes artifacts under
    ``ctx.run_dir``.

    Updates
    -------
    ``ctx.inject_result`` — :class:`InjectResult` with per-file log.
    """
    from .generate import _has_real_rvv_functions

    gen = ctx.current_gen
    valid = (
        gen is not None
        and gen.llm_used
        and _has_real_rvv_functions(gen.generate_plan)
    )
    result = inject_generate_plan(
        ctx.run_dir,
        ctx.repo_root,
        gen.generate_plan if gen is not None else {},
        apply=ctx.apply and valid,
        attempt=attempt,
        cfg=ctx.cfg,
    )
    ctx.inject_result = result
    return ctx
