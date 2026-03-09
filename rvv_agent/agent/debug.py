"""agent.debug — 构建错误修复 Agent

从 agent/chat.py 中提取的构建修复循环逻辑，便于复用与单独测试。
"""
from __future__ import annotations

from dataclasses import dataclass

from ..core.config import AppConfig
from ..core.util import print_llm_error
from .generate import GenerationResult, fix_generation_with_llm


@dataclass
class DebugContext:
    symbol: str
    current_package: dict
    max_retries: int = 3


@dataclass
class DebugResult:
    success: bool
    final_package: dict
    attempts: int
    errors: list[str]


def run_fix_loop(
    cfg: AppConfig,
    ctx: DebugContext,
    get_error_fn,   # () -> tuple[bool, str]  构建并返回 (success, err_text)
    apply_fn=None,  # (package) -> None  生成文件落盘
) -> DebugResult:
    """通用构建修复循环。

    Args:
        cfg: 配置对象
        ctx: 调试上下文（symbol、初始 package、最大重试次数）
        get_error_fn: 回调，运行构建并返回 (build_ok: bool, error_text: str)
        apply_fn: 可选，生成文件重新落盘的回调

    Returns:
        DebugResult
    """
    pkg = ctx.current_package
    errors: list[str] = []

    for attempt in range(ctx.max_retries + 1):
        ok, err_text = get_error_fn()
        if ok:
            return DebugResult(success=True, final_package=pkg, attempts=attempt, errors=errors)

        errors.append(err_text)

        if attempt >= ctx.max_retries:
            break

        fix: GenerationResult = fix_generation_with_llm(cfg, ctx.symbol, err_text, pkg)
        if fix.error:
            print_llm_error(fix.error, f"debug/fix#{attempt+1}")
            break

        pkg = fix.package
        if apply_fn is not None:
            apply_fn(pkg)

    return DebugResult(success=False, final_package=pkg, attempts=attempt + 1, errors=errors)


class DebugAgent:
    """自进化调试 Agent 存根（暂未实现）。"""

    def fix(self, cfg: AppConfig, ctx: DebugContext) -> DebugResult:
        raise NotImplementedError

    def record_outcome(self, ctx: DebugContext, result: DebugResult) -> None:
        raise NotImplementedError

    def suggest_fix_prompt(self, symbol: str, error: str) -> str:
        raise NotImplementedError
