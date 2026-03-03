from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Plan:
    steps: list[str]


def fixed_plan(symbol: str) -> Plan:
    return Plan(
        steps=[
            f"意图解析：迁移 {symbol}",
            "定位 C 实现",
            "定位 x86 / ARM 参考实现",
            "语义抽象（结构化任务描述 JSON）",
            "（MVP）调用 LLM 生成 RVV asm + init + Makefile patch（先落到 runs/）",
            "（可选）把补丁应用到 workspace",
            "（可选）交叉 configure + build checkasm",
            "生成 run 报告（轨迹、输入输出、命令、摘要）",
        ]
    )
