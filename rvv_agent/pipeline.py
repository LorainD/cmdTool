from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .agent.generate import AnalysisResult, analyze_with_llm
from .core.config import AppConfig
from .tool.exec import ExecResult, run_configure, run_make_checkasm
from .agent.generate import (
    GenerationResult,
    fix_generation_with_llm,
    generate_with_llm,
    materialize_package,
)
from .agent.generate import fixed_plan
from .agent.report import write_report
from .agent.search import Discovery, find_symbol
from .core.util import ensure_dir, now_id, slug, write_text


# Maximum number of LLM-fix attempts after a configure/build error
_MAX_FIX_ATTEMPTS = 3


@dataclass
class MigrateResult:
    run_dir: Path
    report_path: Path
    exec_failed: bool
    exec_summary: str


def _has_real_rvv_functions(package: dict) -> bool:
    """Return True only if the generated package contains at least one .S file
    with what appears to be a real RVV implementation (not just a placeholder).

    Heuristics:
    - Has at least one 'files' entry whose path ends in .S
    - That file's content contains a real instruction or RVV intrinsic
      (not just ret/nop or the fallback TODO comment)
    """
    PLACEHOLDER_MARKERS = ("TODO: auto-generated placeholder", ".globl\n")
    REAL_MARKERS = (
        "vsetvli", "vle", "vse", "vadd", "vsub", "vmul", "vfadd", "vfsub",
        "vfmul", "vfneg", "vmv", "vmerge", "viota", "vid", "vfnmacc",
        "vxor", "vneg", "vand", "vor", "lb\t", "lbu\t", "lh\t", "lw\t",
    )
    for f in package.get("files", []):
        path = str(f.get("path", ""))
        content = str(f.get("content", ""))
        if not path.endswith(".S"):
            continue
        if any(m in content for m in PLACEHOLDER_MARKERS):
            return False
        if any(m in content for m in REAL_MARKERS):
            return True
    return False


def run_migrate(
    cfg: AppConfig,
    *,
    symbol: str,
    ffmpeg_root: Path,
    do_exec: bool,
    jobs: int,
    apply: bool,
) -> MigrateResult:
    run_dir = Path("runs") / f"{now_id()}_{slug(symbol)}"
    ensure_dir(run_dir)

    plan = fixed_plan(symbol)

    discovery: Discovery = find_symbol(ffmpeg_root, symbol)

    analysis: AnalysisResult = analyze_with_llm(cfg, discovery)

    gen: GenerationResult = generate_with_llm(cfg, symbol, analysis.analysis)

    # ── Validate that real RVV functions were generated ────────────────────
    # If only a fallback placeholder was produced, record a warning and skip
    # the build step rather than calling the compiler with a stub.
    generation_valid = gen.llm_used and _has_real_rvv_functions(gen.package)
    if not generation_valid:
        # Save the raw LLM output / error for inspection
        write_text(run_dir / "generate_raw.txt", gen.raw_text or "(empty)")
        if not gen.llm_used:
            write_text(
                run_dir / "generate_error.txt",
                f"LLM was not used (fallback). error={gen.error}",
            )
        else:
            write_text(
                run_dir / "generate_error.txt",
                "LLM responded but no real RVV functions found in output.",
            )

    materialized = materialize_package(
        run_dir, ffmpeg_root, gen.package, apply=apply and generation_valid
    )

    exec_result = ExecResult()
    exec_failed = False
    exec_summary = ""

    if do_exec and generation_valid:
        build_dir = ffmpeg_root / cfg.ffmpeg.build_dir
        ensure_dir(build_dir)
        exec_result.configure = run_configure(cfg, ffmpeg_root, build_dir)

        if exec_result.configure.returncode != 0:
            exec_failed = True
            configure_error = (
                (exec_result.configure.stdout or "")
                + (exec_result.configure.stderr or "")
            )
            # ── LLM-fix loop on configure failure ─────────────────────────
            current_gen = gen
            for attempt in range(1, _MAX_FIX_ATTEMPTS + 1):
                write_text(
                    run_dir / f"fix_attempt{attempt}_configure_raw.txt",
                    configure_error,
                )
                fixed_gen = fix_generation_with_llm(
                    cfg,
                    symbol,
                    build_error=configure_error,
                    current_package=current_gen.package,
                )
                write_text(
                    run_dir / f"fix_attempt{attempt}_generate_raw.txt",
                    fixed_gen.raw_text,
                )
                if not fixed_gen.llm_used or not _has_real_rvv_functions(fixed_gen.package):
                    break  # LLM couldn't help
                materialize_package(
                    run_dir, ffmpeg_root, fixed_gen.package,
                    apply=apply, attempt=attempt,
                )
                exec_result.configure = run_configure(cfg, ffmpeg_root, build_dir)
                if exec_result.configure.returncode == 0:
                    exec_failed = False
                    current_gen = fixed_gen
                    break
                configure_error = (
                    (exec_result.configure.stdout or "")
                    + (exec_result.configure.stderr or "")
                )
                current_gen = fixed_gen

        if exec_result.configure is not None and exec_result.configure.returncode == 0:
            build_error_for_fix = ""
            exec_result.make_checkasm = run_make_checkasm(cfg, build_dir, jobs)
            if exec_result.make_checkasm.returncode != 0:
                exec_failed = True
                build_error_for_fix = (
                    (exec_result.make_checkasm.stdout or "")
                    + (exec_result.make_checkasm.stderr or "")
                )
                # ── LLM-fix loop on build (make checkasm) failure ──────────
                current_gen = gen
                for attempt in range(1, _MAX_FIX_ATTEMPTS + 1):
                    write_text(
                        run_dir / f"fix_attempt{attempt}_build_raw.txt",
                        build_error_for_fix,
                    )
                    fixed_gen = fix_generation_with_llm(
                        cfg,
                        symbol,
                        build_error=build_error_for_fix,
                        current_package=current_gen.package,
                    )
                    write_text(
                        run_dir / f"fix_attempt{attempt}_generate_raw.txt",
                        fixed_gen.raw_text,
                    )
                    if not fixed_gen.llm_used or not _has_real_rvv_functions(fixed_gen.package):
                        break
                    materialize_package(
                        run_dir, ffmpeg_root, fixed_gen.package,
                        apply=apply, attempt=attempt,
                    )
                    exec_result.make_checkasm = run_make_checkasm(cfg, build_dir, jobs)
                    if exec_result.make_checkasm.returncode == 0:
                        exec_failed = False
                        break
                    build_error_for_fix = (
                        (exec_result.make_checkasm.stdout or "")
                        + (exec_result.make_checkasm.stderr or "")
                    )
                    current_gen = fixed_gen
        elif exec_result.make_checkasm is None:
            # configure never succeeded – skip checkasm
            pass

        exec_summary = (
            f"generation_valid={generation_valid} "
            f"configure_rc={exec_result.configure.returncode if exec_result.configure else 'skipped'} "
            f"checkasm_build_rc={exec_result.make_checkasm.returncode if exec_result.make_checkasm else 'skipped'}"
        )
    elif not generation_valid:
        exec_summary = "skipped_build: no real RVV functions generated"

    report_path = write_report(
        run_dir,
        plan=plan,
        discovery=discovery,
        analysis=analysis,
        generation_raw=gen.raw_text,
        materialized=materialized,
        exec_result=exec_result,
    )

    return MigrateResult(
        run_dir=run_dir,
        report_path=report_path,
        exec_failed=exec_failed,
        exec_summary=exec_summary,
    )
