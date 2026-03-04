from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from .config import AppConfig
from .util import CmdResult, run_cmd


@dataclass(frozen=True)
class BoardCommands:
    scp_argv: list[str]
    ssh_run_argv: list[str]


def local_checkasm_path(ffmpeg_root: Path, build_dir_name: str) -> Path:
    return ffmpeg_root / build_dir_name / "tests" / "checkasm" / "checkasm"


def build_board_commands(cfg: AppConfig, ffmpeg_root: Path) -> BoardCommands:
    local_bin = local_checkasm_path(ffmpeg_root, str(cfg.ffmpeg.build_dir))
    remote = f"{cfg.board.user}@{cfg.board.host}:{cfg.board.remote_dir}/checkasm"

    scp_argv = [
        "scp",
        "-P",
        str(cfg.board.port),
        str(local_bin),
        remote,
    ]

    ssh_target = f"{cfg.board.user}@{cfg.board.host}"
    ssh_run_argv = [
        "ssh",
        "-p",
        str(cfg.board.port),
        ssh_target,
        f"cd {cfg.board.remote_dir} && chmod +x checkasm && ./checkasm",
    ]

    return BoardCommands(scp_argv=scp_argv, ssh_run_argv=ssh_run_argv)


def run_with_sshpass(argv: list[str], password: str) -> CmdResult:
    # Security note: password will be visible in process list when using -p.
    # Prefer SSH key auth for long-term usage.
    sshpass = shutil.which("sshpass")
    if not sshpass:
        return run_cmd(argv)
    return run_cmd([sshpass, "-p", password, *argv])
