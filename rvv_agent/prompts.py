from __future__ import annotations

import json


def system_prompt() -> str:
    return (
        "你是一个面向 FFmpeg 的 RISC-V Vector (RVV) SIMD 迁移助手，同时也可以进行普通技术对话。"
        "当且仅当用户明确要做迁移/生成/编译/运行等动作时，你才进入迁移任务流程。"
        "在涉及修改代码、编译、scp、远程运行前，必须让人类确认。"
    )


def intent_prompt(user_text: str) -> str:
    return f"""你将解析用户意图，并输出严格 JSON（不要额外文字）。

用户输入：{user_text}

输出 JSON schema：
{{
  \"action\": \"chat\" | \"migrate\",
  \"symbol\": \"任意 C 函数/算子标识符\" | \"\" ,
  \"notes\": \"...\"
}}

判定规则（尽量保守）：
- 只有当用户明确表达“迁移/生成 RVV/做 SIMD 优化/改 FFmpeg 代码/编译/跑 checkasm/把算子迁移到 RVV”等意图时，action=\"migrate\"。
- 否则 action=\"chat\"。
- 如果 action=migrate 且能从输入中确定要迁移的函数/算子名（可能不是 ff_*），symbol 填该标识符；否则 symbol 为空串。
"""


def retrieval_prompt(symbol: str, grouped: dict, matches: list) -> str:
    grouped_s = json.dumps(grouped, ensure_ascii=False)
    matches_s = "\n".join(str(m) for m in matches)

    return f"""你将根据检索分组结果，选择最相关的参考文件列表，并输出严格 JSON（不要额外文字）。

目标 symbol：{symbol}

候选分组（文件路径列表，JSON）：
{grouped_s}

部分命中行：
{matches_s}

输出 JSON schema：
{{
  \"symbol\": \"{symbol}\",
  \"c\": [\"...\"],
  \"x86\": [\"...\"],
  \"arm\": [\"...\"],
  \"riscv\": [\"...\"],
  \"headers\": [\"...\"],
  \"makefiles\": [\"...\"],
  \"checkasm\": [\"...\"],
  \"notes\": \"...\"
}}

要求：
- 每个列表最多 5 个文件。
- makefiles 至少包含 libavcodec/riscv/Makefile（如果不存在也照写）。
- checkasm 建议给出 tests/checkasm/checkasm.c（若无法确定可留空数组）。
"""


def analysis_prompt(symbol: str, context: str) -> str:
    return f"""任务：迁移/生成 {symbol} 的 RVV 优化。

请基于下面的上下文（来自 workspace 的源码片段）输出一个严格 JSON（不要额外文字），字段如下：

{{
  \"symbol\": \"{symbol}\",
  \"datatype\": \"unknown|int16|int32|uint8|...\",
  \"vectorizable\": true|false,
  \"pattern\": [\"butterfly\", \"horizontal_add\", \"stride_load\", \"saturate\", \"tail\"],
  \"has_stride\": true|false,
  \"has_saturation\": true|false,
  \"reduction\": true|false,
  \"tail_required\": true|false,
  \"math_expression\": \"...\",
  \"c_candidates\": [\"path:line\", ...],
  \"x86_refs\": [\"path:line\", ...],
  \"arm_refs\": [\"path:line\", ...],
  \"notes\": \"...\"
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
  \"files\": [{{\"path\": \"...\", \"content\": \"...\"}}, ...],
  \"patches\": [{{\"path\": \"...\", \"diff\": \"...\"}}, ...]
}}

analysis_json:
{analysis_json}
"""
