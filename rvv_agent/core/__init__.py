"""core: 基础层 — 配置、工具函数、LLM 客户端、Prompt 模板、状态机。
不依赖 agent/tool/memory 中的任何模块。
"""

from .config import (
    AppConfig, BoardConfig, FfmpegConfig, HumanConfig, LlmConfig, ToolchainConfig, load_config,
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
from .context import *  # DEPRECATED: MigrationContext 已注释，保留 import 不报错
from .task import (
    ArtifactIndex, TaskContext, TaskState, MigrationTarget,
    RetrievalArtifact, AnalysisArtifact, PlanArtifact,
    PatchPoint, PatchDesign, PatchArtifact,
    BuildArtifact, DebugArtifact, KBUpdateArtifact,
)
from .statemachine import StateMachine

__all__ = [
    # config
    "AppConfig", "BoardConfig", "FfmpegConfig", "HumanConfig",
    "LlmConfig", "ToolchainConfig", "load_config",
    # llm
    "LlmError", "LlmMessage", "api_key_present", "chat_completion",
    "get_trajectory_dict", "probe_llm", "reset_trajectory",
    # util
    "CmdResult", "ensure_dir", "extract_build_errors", "fmt_argv", "now_id",
    "print_llm_error", "print_red", "print_yellow",
    "run_cmd", "run_cmd_stream", "slug", "write_json", "write_text",
    # context (pipeline) — DEPRECATED, MigrationContext 已注释
    # "MigrationContext",
    # task (state machine)
    "ArtifactIndex", "TaskContext", "TaskState", "MigrationTarget",
    "RetrievalArtifact", "AnalysisArtifact", "PlanArtifact",
    "PatchPoint", "PatchDesign", "PatchArtifact",
    "BuildArtifact", "DebugArtifact", "KBUpdateArtifact",
    # statemachine
    "StateMachine",
]
