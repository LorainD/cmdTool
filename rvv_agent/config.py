from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class LlmConfig:
    base_url: str = "https://api.openai.com/v1"
    api_key_env: str = "LLM_API_KEY"
    model: str = "gpt-5.2-mini"
    temperature: float = 0.2


@dataclass
class ToolchainConfig:
    cross_prefix: str = "riscv64-unknown-linux-gnu-"
    arch: str = "riscv64"
    target_os: str = "linux"
    cpu: str = "rv64gcv"
    extra_cflags: str = "-march=rv64gcv -mabi=lp64d -O3"
    extra_ldflags: str = "-static"


@dataclass
class FfmpegConfig:
    root: Path = Path("workplace/FFmpeg")
    build_dir: Path = Path("build")
    configure_path: Path | None = None
    configure_extra_args: list[str] = field(default_factory=list)


@dataclass
class BoardConfig:
    enabled: bool = False
    user: str = ""
    host: str = ""
    port: int = 22
    remote_dir: str = "workplace"


@dataclass
class AppConfig:
    llm: LlmConfig = field(default_factory=LlmConfig)
    toolchain: ToolchainConfig = field(default_factory=ToolchainConfig)
    ffmpeg: FfmpegConfig = field(default_factory=FfmpegConfig)
    board: BoardConfig = field(default_factory=BoardConfig)


def _as_path(v: object) -> Path | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    return Path(s)


def load_config(path: Path | None) -> AppConfig:
    cfg = AppConfig()
    if path is None:
        path = Path("rvv_agent.toml")

    if path.exists():
        raw = tomllib.loads(path.read_text(encoding="utf-8"))

        llm = raw.get("llm", {})
        cfg.llm.base_url = str(llm.get("base_url", cfg.llm.base_url)).rstrip("/")
        cfg.llm.api_key_env = str(llm.get("api_key_env", cfg.llm.api_key_env))
        cfg.llm.model = str(llm.get("model", cfg.llm.model))
        cfg.llm.temperature = float(llm.get("temperature", cfg.llm.temperature))

        tc = raw.get("toolchain", {})
        cfg.toolchain.cross_prefix = str(tc.get("cross_prefix", cfg.toolchain.cross_prefix))
        cfg.toolchain.arch = str(tc.get("arch", cfg.toolchain.arch))
        cfg.toolchain.target_os = str(tc.get("target_os", cfg.toolchain.target_os))
        cfg.toolchain.cpu = str(tc.get("cpu", cfg.toolchain.cpu))
        cfg.toolchain.extra_cflags = str(tc.get("extra_cflags", cfg.toolchain.extra_cflags))
        cfg.toolchain.extra_ldflags = str(tc.get("extra_ldflags", cfg.toolchain.extra_ldflags))

        ff = raw.get("ffmpeg", {})
        cfg.ffmpeg.root = Path(ff.get("root", str(cfg.ffmpeg.root)))
        cfg.ffmpeg.build_dir = Path(ff.get("build_dir", str(cfg.ffmpeg.build_dir)))
        cfg.ffmpeg.configure_path = _as_path(ff.get("configure_path"))
        cfg.ffmpeg.configure_extra_args = list(ff.get("configure_extra_args", cfg.ffmpeg.configure_extra_args))

        bd = raw.get("board", {})
        cfg.board.enabled = bool(bd.get("enabled", cfg.board.enabled))
        cfg.board.user = str(bd.get("user", cfg.board.user))
        cfg.board.host = str(bd.get("host", cfg.board.host))
        cfg.board.port = int(bd.get("port", cfg.board.port))
        cfg.board.remote_dir = str(bd.get("remote_dir", cfg.board.remote_dir))

    if os.getenv("RVV_AGENT_FFMPEG_ROOT"):
        cfg.ffmpeg.root = Path(os.environ["RVV_AGENT_FFMPEG_ROOT"])

    return cfg
