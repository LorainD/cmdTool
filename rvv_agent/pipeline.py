from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .analyze import AnalysisResult, analyze_with_llm
from .config import AppConfig
from .exec import ExecResult, run_configure, run_make_checkasm
from .generate import GenerationResult, generate_with_llm, materialize_package
from .plan import fixed_plan
from .report import write_report
from .search import Discovery, find_symbol
from .util import ensure_dir, now_id, slug


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
    run_dir = Path("runs") / f"{now_id()}_{slug(symbol)}"
    ensure_dir(run_dir)

    plan = fixed_plan(symbol)

    discovery: Discovery = find_symbol(ffmpeg_root, symbol)

    analysis: AnalysisResult = analyze_with_llm(cfg, discovery)

    gen: GenerationResult = generate_with_llm(cfg, symbol, analysis.analysis)

    materialized = materialize_package(run_dir, ffmpeg_root, gen.package, apply=apply)

    exec_result = ExecResult()
    exec_failed = False
    exec_summary = ""

    if do_exec:
        build_dir = ffmpeg_root / cfg.ffmpeg.build_dir
        ensure_dir(build_dir)
        exec_result.configure = run_configure(cfg, ffmpeg_root, build_dir)
        if exec_result.configure.returncode != 0:
            exec_failed = True

        exec_result.make_checkasm = run_make_checkasm(build_dir, jobs)
        if exec_result.make_checkasm.returncode != 0:
            exec_failed = True

        exec_summary = (
            f"configure_rc={exec_result.configure.returncode} "
            f"checkasm_build_rc={exec_result.make_checkasm.returncode}"
        )

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
