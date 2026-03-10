from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .agent.generate import (
    AnalysisResult,
    GenerationResult,
    analyze as analyze_stage,
    fix_generation_with_llm,
    generate as generate_stage,
    save_generate_folder,
    _has_real_rvv_functions,
    fixed_plan,
)
from .agent.inject import InjectResult, inject_generate_plan, insert as insert_stage
from .agent.report import write_report
from .agent.search import Discovery, find_symbol, search as search_stage
from .core.config import AppConfig
from .core.context import MigrationContext
from .core.util import ensure_dir, now_id, slug, write_text
from .tool.exec import ExecResult, run_configure, run_make_checkasm


_MAX_FIX_ATTEMPTS = 3


@dataclass
class MigrateResult:
    run_dir: Path
    report_path: Path
    exec_failed: bool
    exec_summary: str


def run_migrate(
    cfg: AppConfig,
    *,
    symbol: str,
    ffmpeg_root: Path,
    do_exec: bool,
    jobs: int,
    apply: bool,
) -> MigrateResult:
    """Two-stage migration pipeline.

    Stage 1 — Generator LLM
        Produces code snippets (only new code) saved under run_dir/generate/.

    Stage 2 — Injector (+ optional Locator LLM for init.c / Makefile)
        Reads run_dir/generate/plan.json and applies safe append/create to
        the FFmpeg workspace.  Results logged under run_dir/apply/.
    """
    # ── Initialise context ────────────────────────────────────────────────
    ctx = MigrationContext(
        operator=symbol,
        repo_root=ffmpeg_root,
        cfg=cfg,
        do_exec=do_exec,
        apply=apply,
        jobs=jobs,
    )
    ctx.run_dir = Path("runs") / f"{now_id()}_{slug(symbol)}"
    ensure_dir(ctx.run_dir)

    plan = fixed_plan(symbol)

    # ── Stage: Search (locate symbol in the source tree) ──────────────────
    ctx = search_stage(ctx)

    # ── Stage: Analyze (LLM semantic analysis) ────────────────────────────
    ctx = analyze_stage(ctx)

    # ── Stage: Generate (LLM code generation) ─────────────────────────────
    ctx = generate_stage(ctx)

    gen = ctx.current_gen
    generation_valid = gen.llm_used and _has_real_rvv_functions(gen.generate_plan)

    if not generation_valid:
        write_text(ctx.run_dir / "generate_raw.txt", gen.raw_text or "(empty)")
        write_text(
            ctx.run_dir / "generate_error.txt",
            f"LLM was not used (fallback). error={gen.error}"
            if not gen.llm_used
            else "LLM responded but no real RVV functions found in output.",
        )

    # ── Stage: Inject (apply generated code to workspace) ─────────────────
    ctx = insert_stage(ctx)

    # ── Stage: Build + LLM fix loops ──────────────────────────────────────
    ctx.exec_result = ExecResult()
    exec_failed = False
    exec_summary = ""

    if do_exec and generation_valid:
        build_dir = ffmpeg_root / cfg.ffmpeg.build_dir
        ensure_dir(build_dir)
        ctx.exec_result.configure = run_configure(cfg, ffmpeg_root, build_dir)

        if ctx.exec_result.configure.returncode != 0:
            exec_failed = True
            ctx.build_log = (
                (ctx.exec_result.configure.stdout or "")
                + (ctx.exec_result.configure.stderr or "")
            )
            # ── LLM-fix loop on configure failure ─────────────────────────
            for attempt in range(1, _MAX_FIX_ATTEMPTS + 1):
                write_text(
                    ctx.run_dir / f"fix_attempt{attempt}_configure_error.txt",
                    ctx.build_log,
                )
                fixed_gen = fix_generation_with_llm(
                    cfg, symbol,
                    build_error=ctx.build_log,
                    current_plan=ctx.current_gen.generate_plan,
                )
                write_text(
                    ctx.run_dir / f"fix_attempt{attempt}_generate_raw.txt",
                    fixed_gen.raw_text,
                )
                if not fixed_gen.llm_used or not _has_real_rvv_functions(fixed_gen.generate_plan):
                    break
                ctx.current_gen = fixed_gen
                save_generate_folder(ctx.run_dir, fixed_gen.generate_plan, attempt=attempt)
                inject_generate_plan(
                    ctx.run_dir, ffmpeg_root, fixed_gen.generate_plan,
                    apply=apply, attempt=attempt, cfg=cfg,
                )
                ctx.exec_result.configure = run_configure(cfg, ffmpeg_root, build_dir)
                if ctx.exec_result.configure.returncode == 0:
                    exec_failed = False
                    break
                ctx.build_log = (
                    (ctx.exec_result.configure.stdout or "")
                    + (ctx.exec_result.configure.stderr or "")
                )

        if ctx.exec_result.configure is not None and ctx.exec_result.configure.returncode == 0:
            ctx.exec_result.make_checkasm = run_make_checkasm(cfg, build_dir, jobs)
            if ctx.exec_result.make_checkasm.returncode != 0:
                exec_failed = True
                ctx.build_log = (
                    (ctx.exec_result.make_checkasm.stdout or "")
                    + (ctx.exec_result.make_checkasm.stderr or "")
                )
                ctx.checkasm_output = ctx.build_log
                # ── LLM-fix loop on make checkasm failure ──────────────────
                for attempt in range(1, _MAX_FIX_ATTEMPTS + 1):
                    write_text(
                        ctx.run_dir / f"fix_attempt{attempt}_build_error.txt",
                        ctx.build_log,
                    )
                    fixed_gen = fix_generation_with_llm(
                        cfg, symbol,
                        build_error=ctx.build_log,
                        current_plan=ctx.current_gen.generate_plan,
                    )
                    write_text(
                        ctx.run_dir / f"fix_attempt{attempt}_generate_raw.txt",
                        fixed_gen.raw_text,
                    )
                    if not fixed_gen.llm_used or not _has_real_rvv_functions(fixed_gen.generate_plan):
                        break
                    ctx.current_gen = fixed_gen
                    save_generate_folder(ctx.run_dir, fixed_gen.generate_plan, attempt=attempt)
                    inject_generate_plan(
                        ctx.run_dir, ffmpeg_root, fixed_gen.generate_plan,
                        apply=apply, attempt=attempt, cfg=cfg,
                    )
                    ctx.exec_result.make_checkasm = run_make_checkasm(cfg, build_dir, jobs)
                    if ctx.exec_result.make_checkasm.returncode == 0:
                        exec_failed = False
                        ctx.checkasm_output = (
                            (ctx.exec_result.make_checkasm.stdout or "")
                            + (ctx.exec_result.make_checkasm.stderr or "")
                        )
                        break
                    ctx.build_log = (
                        (ctx.exec_result.make_checkasm.stdout or "")
                        + (ctx.exec_result.make_checkasm.stderr or "")
                    )
                    ctx.checkasm_output = ctx.build_log

        exec_summary = (
            f"generation_valid={generation_valid} "
            f"configure_rc={ctx.exec_result.configure.returncode if ctx.exec_result.configure else 'skipped'} "
            f"checkasm_build_rc={ctx.exec_result.make_checkasm.returncode if ctx.exec_result.make_checkasm else 'skipped'}"
        )
    elif not generation_valid:
        exec_summary = "skipped_build: no real RVV functions generated"

    report_path = write_report(
        ctx.run_dir,
        plan=plan,
        discovery=ctx.discovery,
        analysis=ctx.analysis_result,
        generation_raw=ctx.current_gen.raw_text if ctx.current_gen else "",
        materialized=ctx.inject_result.applied_paths if ctx.inject_result else [],
        exec_result=ctx.exec_result,
    )

    return MigrateResult(
        run_dir=ctx.run_dir,
        report_path=report_path,
        exec_failed=exec_failed,
        exec_summary=exec_summary,
    )
