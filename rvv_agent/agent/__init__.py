"""agent: Agent 层 —— 意图解析、搜索/检索、分析、代码生成、构建修复、报告、交互对话。

子模块分工
----------
chat     — 交互式 human-in-the-loop 迁移循环（SessionContext / run_chat）
debug    — 非交互式 LLM 构建错误修复循环（run_fix_loop）
generate — 计划生成、语义分析、LLM 代码生成与修复（Plan / AnalysisResult / GenerationResult）
intent   — 用户意图解析（Intent / parse_intent）
report   — 运行报告落盘（write_report）
search   — 源码搜索、参考文件检索、上下文构建（Discovery / RetrievalResult / select_references）

自进化预留接口
--------------
agent.evolve（尚未实现）将与 memory.pattern_lib 交互，
把每次迁移的经验沉淀为可复用的 RVV 代码模式。
"""

from .chat import SessionContext, SessionState, run_chat
from .debug import run_fix_loop
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
from .report import write_report
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
    "SessionContext", "SessionState", "run_chat",
    # debug
    "run_fix_loop",
    # generate
    "AnalysisResult", "GenerationResult", "Plan",
    "analyze_with_llm", "fix_generation_with_llm", "fixed_plan",
    "generate_with_llm", "llm_plan", "materialize_package",
    # intent
    "Intent", "parse_intent",
    # report
    "write_report",
    # search
    "Discovery", "Match", "RetrievalResult",
    "build_context_from_files", "find_symbol", "find_symbol_multi", "select_references",
]
