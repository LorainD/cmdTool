from __future__ import annotations

import datetime as dt
import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CmdResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str


def now_id() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", text.strip())
    return s[:120] if len(s) > 120 else s


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def write_text(p: Path, content: str) -> None:
    ensure_dir(p.parent)
    p.write_text(content, encoding="utf-8")


def write_json(p: Path, obj: object) -> None:
    write_text(p, json.dumps(obj, ensure_ascii=False, indent=2))


def fmt_argv(argv: list[str]) -> str:
    return " ".join(shlex.quote(a) for a in argv)


def run_cmd(argv: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> CmdResult:
    merged = os.environ.copy()
    if env:
        merged.update(env)

    p = subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        env=merged,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    return CmdResult(argv=list(argv), returncode=p.returncode, stdout=p.stdout, stderr=p.stderr)


def run_cmd_stream(
    argv: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> CmdResult:
    """Run a command, printing stdout/stderr in real time and returning full captured output."""
    import sys
    merged = os.environ.copy()
    if env:
        merged.update(env)

    proc = subprocess.Popen(
        argv,
        cwd=str(cwd) if cwd else None,
        env=merged,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,  # merge stderr into stdout for unified stream
    )

    out_lines: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        out_lines.append(line)
    proc.wait()
    combined = "".join(out_lines)
    return CmdResult(argv=list(argv), returncode=proc.returncode, stdout=combined, stderr="")
