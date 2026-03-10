"""agent.debug — Build-error Fix Agent

Provides run_fix_loop() for use in interactive (chat) mode.
The non-interactive pipeline fix loops live in pipeline.py.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..core.config import AppConfig
from ..core.util import print_llm_error
from .generate import GenerationResult, _has_real_rvv_functions, fix_generation_with_llm


@dataclass
class DebugContext:
    symbol: str
    current_plan: dict        # generate_plan dict ({generated:[...]})
    max_retries: int = 3


@dataclass
class DebugResult:
    success: bool
    final_plan: dict          # generate_plan dict after fix attempts
    attempts: int
    errors: list


def run_fix_loop(
    cfg: AppConfig,
    ctx: DebugContext,
    get_error_fn,   # () -> tuple[bool, str]
    apply_fn=None,  # (generate_plan: dict) -> None
) -> DebugResult:
    """Generic build-fix loop for interactive (chat) mode.

    Args:
        cfg:           App config.
        ctx:           DebugContext with symbol, initial generate_plan, max retries.
        get_error_fn:  Callable returning (build_ok: bool, error_text: str).
        apply_fn:      Optional callback to re-inject after each fix attempt.
                       Receives the new generate_plan dict.
    Returns:
        DebugResult
    """
    plan = ctx.current_plan
    errors = []

    for attempt in range(ctx.max_retries + 1):
        ok, err_text = get_error_fn()
        if ok:
            return DebugResult(success=True, final_plan=plan,
                               attempts=attempt, errors=errors)
        errors.append(err_text)

        if attempt >= ctx.max_retries:
            break

        fix: GenerationResult = fix_generation_with_llm(
            cfg, ctx.symbol, err_text, plan
        )
        if fix.error:
            print_llm_error(fix.error, f"debug/fix#{attempt+1}")
            break
        if not _has_real_rvv_functions(fix.generate_plan):
            break

        plan = fix.generate_plan
        if apply_fn is not None:
            apply_fn(plan)

    return DebugResult(success=False, final_plan=plan,
                       attempts=attempt + 1, errors=errors)


class DebugAgent:
    """Self-evolving debug agent stub (not yet implemented)."""

    def fix(self, cfg: AppConfig, ctx: DebugContext) -> DebugResult:
        raise NotImplementedError

    def record_outcome(self, ctx: DebugContext, result: DebugResult) -> None:
        raise NotImplementedError

    def suggest_fix_prompt(self, symbol: str, error: str) -> str:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Context-aware stage wrapper
# ---------------------------------------------------------------------------

def debug(ctx: "MigrationContext") -> "MigrationContext":
    """Context-aware single-attempt LLM fix stage.

    Reads ``ctx.build_log`` and ``ctx.current_gen``, calls
    :func:`fix_generation_with_llm` once, and updates ``ctx.current_gen``
    with the result if the LLM produced valid RVV code.

    The *pipeline* is responsible for looping (calling debug + insert + build
    repeatedly up to ``_MAX_FIX_ATTEMPTS``).

    Returns *ctx* unchanged if there is nothing to fix (no log or no error).

    Updates
    -------
    ``ctx.current_gen`` — updated :class:`GenerationResult` if fix succeeded.
    """
    if ctx.build_log is None or ctx.current_gen is None:
        return ctx
    if "error" not in ctx.build_log.lower():
        return ctx

    fixed_gen = fix_generation_with_llm(
        ctx.cfg,
        ctx.operator,
        ctx.build_log,
        ctx.current_gen.generate_plan,
    )
    if fixed_gen.llm_used and _has_real_rvv_functions(fixed_gen.generate_plan):
        ctx.current_gen = fixed_gen
    return ctx
