"""core: 基础层 —— 配置、工具函数、LLM 客户端、Prompt 模板。
不依赖 agent/tool/memory 中的任何模块。
"""

from .config import (
    AppConfig, BoardConfig, FfmpegConfig, LlmConfig, ToolchainConfig, load_config,
)
from .llm import (
    LlmError, LlmMessage, api_key_present, chat_completion,
    get_trajectory_dict, probe_llm, reset_trajectory,
)
from .util import (
    CmdResult, ensure_dir, extract_build_errors, fmt_argv, now_id,
    print_llm_error, print_red, print_yellow,
    run_cmd, run_cmd_stream, slug, write_json, write_text,
)

__all__ = [
    "AppConfig", "BoardConfig", "FfmpegConfig", "LlmConfig", "ToolchainConfig", "load_config",
    "LlmError", "LlmMessage", "api_key_present", "chat_completion",
    "get_trajectory_dict", "probe_llm", "reset_trajectory",
    "CmdResult", "ensure_dir", "extract_build_errors", "fmt_argv", "now_id",
    "print_llm_error", "print_red", "print_yellow",
    "run_cmd", "run_cmd_stream", "slug", "write_json", "write_text",
]
