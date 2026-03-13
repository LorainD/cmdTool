"""agent: Agent 层 — 意图解析、搜索/检索、分析、代码生成、构建修复、报告、交互对话。

子模块分工
----------
chat     — 交互式 human-in-the-loop 迁移循环（状态机驱动）
debug    — 结构化构建错误诊断 + 回滚决策
generate — 计划生成、语义分析、LLM 代码生成与修复（pipeline 模式）
inject   — 代码注入（pipeline 模式）
intent   — 用户意图解析（Intent / parse_intent）
patch    — 4步 PATCH 阶段（locate/design/generate/apply，chat 模式）
report   — 运行报告落盘（write_report）
search   — 源码搜索、参考文件检索、上下文构建
"""

from .chat import run_chat
from .debug import run_fix_loop, run_debug_handler
from .generate import (
    AnalysisResult,
    GenerationResult,
    Plan,
    analyze_with_llm,
    fix_generation_with_llm,
    fixed_plan,
    generate_with_llm,
    llm_plan,
    materialize_package,
)
from .intent import Intent, parse_intent
from .report import write_report, write_chat_report
from .search import (
    Discovery,
    Match,
    RetrievalResult,
    build_context_from_files,
    find_symbol,
    find_symbol_multi,
    select_references,
)

__all__ = [
    # chat
    "run_chat",
    # debug
    "run_fix_loop", "run_debug_handler",
    # generate
    "AnalysisResult", "GenerationResult", "Plan",
    "analyze_with_llm", "fix_generation_with_llm", "fixed_plan",
    "generate_with_llm", "llm_plan", "materialize_package",
    # intent
    "Intent", "parse_intent",
    # report
    "write_report", "write_chat_report",
    # search
    "Discovery", "Match", "RetrievalResult",
    "build_context_from_files", "find_symbol", "find_symbol_multi", "select_references",
]
