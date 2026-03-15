from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..core.config import AppConfig
from ..core.util import CmdResult, run_cmd_stream


@dataclass
class ExecResult:
    configure: CmdResult | None = None
    make_checkasm: CmdResult | None = None


def configure_argv(cfg: AppConfig, ffmpeg_root: Path) -> list[str]:
    configure = cfg.ffmpeg.configure_path
    if configure is None:
        configure = ffmpeg_root / "configure"
    return [
        str(configure),
        f"--cross-prefix={cfg.toolchain.cross_prefix}",
        f"--arch={cfg.toolchain.arch}",
        f"--target-os={cfg.toolchain.target_os}",
        "--enable-cross-compile",
        f"--cpu={cfg.toolchain.cpu}",
        f"--extra-cflags={cfg.toolchain.extra_cflags}",
        f"--extra-ldflags={cfg.toolchain.extra_ldflags}",
        "--disable-shared",
        "--enable-static",
        *cfg.ffmpeg.configure_extra_args,
    ]


def make_checkasm_argv(jobs: int) -> list[str]:
    return ["make", f"-j{jobs}", "tests/checkasm/checkasm"]


def _toolchain_env(cfg: AppConfig) -> dict[str, str] | None:
    extra = cfg.toolchain.extra_path.strip()
    if not extra:
        return None
    current = os.environ.get("PATH", "")
    env = os.environ.copy()
    env["PATH"] = f"{extra}:{current}" if current else extra
    return env


def run_configure(cfg: AppConfig, ffmpeg_root: Path, build_dir: Path) -> CmdResult:
    """交叉编译 configure，实时输出日志。"""
    return run_cmd_stream(configure_argv(cfg, ffmpeg_root), cwd=build_dir,
                          env=_toolchain_env(cfg))


def run_make_checkasm(cfg: AppConfig, build_dir: Path, jobs: int) -> CmdResult:
    """构建 checkasm，实时输出日志。"""
    return run_cmd_stream(make_checkasm_argv(jobs), cwd=build_dir,
                          env=_toolchain_env(cfg))


# ---------------------------------------------------------------------------
# Context-aware stage wrapper (DEPRECATED — 已被状态机架构替代)
# ---------------------------------------------------------------------------
# def build(ctx: "MigrationContext") -> "MigrationContext":
#     """Context-aware build stage.
#
#     .. deprecated::
#         Pipeline now uses state-machine handlers. Kept for backward compat.
#     """
#     from ..core.util import ensure_dir as _ensure_dir
#
#     build_dir = ctx.repo_root / ctx.cfg.ffmpeg.build_dir
#     _ensure_dir(build_dir)
#
#     if ctx.exec_result is None:
#         ctx.exec_result = ExecResult()
#
#     configure_result = run_configure(ctx.cfg, ctx.repo_root, build_dir)
#     ctx.exec_result.configure = configure_result
#     ctx.build_log = (configure_result.stdout or "") + (configure_result.stderr or "")
#
#     if configure_result.returncode == 0:
#         make_result = run_make_checkasm(ctx.cfg, build_dir, ctx.jobs)
#         ctx.exec_result.make_checkasm = make_result
#         ctx.checkasm_output = (make_result.stdout or "") + (make_result.stderr or "")
#
#     return ctx
