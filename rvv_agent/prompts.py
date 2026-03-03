from __future__ import annotations


def system_prompt() -> str:
    return (
        "你是一个面向 FFmpeg 的 RISC-V Vector (RVV) SIMD 迁移助手。"
        "你输出必须工程化、可执行、可落盘。"
    )


def analysis_prompt(symbol: str, context: str) -> str:
    return f"""任务：迁移/生成 {symbol} 的 RVV 优化。

请基于下面的上下文（来自 workspace 的源码检索片段）输出一个严格 JSON（不要额外文字），字段如下：

{{
  "symbol": "{symbol}",
  "datatype": "unknown|int16|int32|uint8|...",
  "vectorizable": true|false,
  "pattern": ["butterfly", "horizontal_add", "stride_load", "saturate", "tail"],
  "has_stride": true|false,
  "has_saturation": true|false,
  "reduction": true|false,
  "tail_required": true|false,
  "math_expression": "...",
  "c_candidates": ["path:line", ...],
  "x86_refs": ["path:line", ...],
  "arm_refs": ["path:line", ...],
  "notes": "..."
}}

上下文：
{context}
"""


def generation_prompt(symbol: str, analysis_json: str) -> str:
    return f"""基于下面的 JSON 分析，生成一个最小可编译的补丁包（不要解释）。

要求：
1) 生成 RVV asm 文件内容（.S），提供建议的落点路径（例如 libavcodec/riscv/<name>_rvv.S）
2) 生成 init .c 的增量建议（以 unified diff 或明确的片段说明）
3) 生成 Makefile 的增量建议（以 unified diff 或明确的片段说明）
4) 如果无法确定确切落点/宏名，给出最保守的 TODO patch，但仍需保持格式可被人类直接应用。

输出格式必须是严格 JSON（不要额外文字）：
{{
  "files": [{{"path": "...", "content": "..."}}, ...],
  "patches": [{{"path": "...", "diff": "..."}}, ...]
}}

analysis_json:
{analysis_json}
"""
