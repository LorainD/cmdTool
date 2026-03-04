from __future__ import annotations

import json
from dataclasses import dataclass

from .config import AppConfig
from .llm import LlmError, LlmMessage, chat_completion
from .prompts import plan_prompt, system_prompt


@dataclass
class Plan:
    steps: list[str]


def fixed_plan(symbol: str) -> Plan:
    """Hardcoded fallback plan used when LLM is unavailable."""
    return Plan(
        steps=[
            f"意图解析：迁移 {symbol}",
            "定位 C 实现",
            "定位 x86 / ARM 参考实现",
            "语义抽象（结构化任务描述 JSON）",
            f"调用 LLM 生成 {symbol} 的 RVV asm + init + Makefile patch（落到 runs/）",
            "（可选）把补丁应用到 workspace",
            "（可选）交叉 configure + build checkasm",
            "生成 run 报告（轨迹、输入输出、命令、摘要）",
        ]
    )


def llm_plan(cfg: AppConfig, symbol: str) -> Plan:
    """Ask the LLM to generate a symbol-specific migration plan.

    Falls back to fixed_plan() if the LLM is unavailable or returns invalid JSON.
    """
    messages = [
        LlmMessage(role="system", content=system_prompt()),
        LlmMessage(role="user", content=plan_prompt(symbol)),
    ]
    try:
        raw = chat_completion(cfg.llm, messages, max_tokens=600)
        raw = raw.strip()
        # Extract JSON object
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end > start:
            raw = raw[start : end + 1]
        data = json.loads(raw)
        steps = data.get("steps", [])
        if not isinstance(steps, list) or not steps:
            raise ValueError("empty steps")
        return Plan(steps=[str(s).strip() for s in steps if str(s).strip()])
    except (LlmError, Exception):
        return fixed_plan(symbol)
