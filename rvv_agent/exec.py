from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import AppConfig
from .util import CmdResult, run_cmd


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


def run_configure(cfg: AppConfig, ffmpeg_root: Path, build_dir: Path) -> CmdResult:
    return run_cmd(configure_argv(cfg, ffmpeg_root), cwd=build_dir)


def run_make_checkasm(build_dir: Path, jobs: int) -> CmdResult:
    return run_cmd(make_checkasm_argv(jobs), cwd=build_dir)
