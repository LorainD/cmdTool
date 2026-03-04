from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import os

from .config import AppConfig
from .util import CmdResult, run_cmd, run_cmd_stream


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
    """Return an environment dict with extra_path prepended to PATH, or None."""
    extra = cfg.toolchain.extra_path.strip()
    if not extra:
        return None
    current = os.environ.get("PATH", "")
    new_path = f"{extra}:{current}" if current else extra
    env = os.environ.copy()
    env["PATH"] = new_path
    return env


def run_configure(cfg: AppConfig, ffmpeg_root: Path, build_dir: Path) -> CmdResult:
    """Run configure from build_dir with real-time output streaming.
    Injects toolchain.extra_path into PATH so the cross-compiler is found.
    """
    return run_cmd_stream(configure_argv(cfg, ffmpeg_root), cwd=build_dir,
                          env=_toolchain_env(cfg))


def run_make_checkasm(cfg: AppConfig, build_dir: Path, jobs: int) -> CmdResult:
    """Run make checkasm with real-time output streaming.
    Injects toolchain.extra_path into PATH so the cross-compiler is found.
    """
    return run_cmd_stream(make_checkasm_argv(jobs), cwd=build_dir,
                          env=_toolchain_env(cfg))
