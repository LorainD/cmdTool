from __future__ import annotations

import json


def system_prompt() -> str:
    return (
        "你是一个面向 FFmpeg 的 RISC-V Vector (RVV) SIMD 迁移专家，同时也可以进行普通技术对话。"
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
- **x86** 和 **arm** 列表中必须优先包含 .S / .asm 等实际 SIMD 实现文件（如 sbrdsp.asm、sbrdsp_neon.S），
  而不仅仅是 *_init*.c 注册文件——*_init*.c 只是函数指针赋值，真正的向量实现在汇编文件里。
- 若 x86_refs 或 arm_refs 中同时有 init.c 和 .S/.asm，请把 .S/.asm 放在前面。
"""


def analysis_prompt(symbol: str, context: str) -> str:
    return f"""任务：迁移/生成 {symbol} 的 RVV 优化。

请基于下面的上下文（来自 workspace 的源码片段）输出一个严格 JSON（不要额外文字），字段如下：

{{
  \"symbol\": \"{symbol}\",
  \"datatype\": \"float32|float64|int16|int32|int64|uint8|uint16|uint32|mixed\",
  \"vectorizable\": true|false,
  \"pattern\": [\"butterfly\", \"horizontal_add\", \"stride_load\", \"saturate\", \"tail\"],
  \"has_stride\": true|false,
  \"has_saturation\": true|false,
  \"reduction\": true|false,
  \"tail_required\": true|false,
  \"math_expression\": \"精准数学伪代码，如 dst[i+1]=-dst[i+1] 或 out[i]=a[i]*b[i]\",
  \"c_candidates\": [\"path:line\", ...],
  \"x86_refs\": [\"path:line\", ...],   // 优先包含 .S/.asm 实际 SIMD 实现，而非仅 init.c
  \"arm_refs\": [\"path:line\", ...],   // 同上，NEON .S 文件比 init_arm.c 更重要
  \"notes\": \"...\"
}}

注意：
- datatype 必须推断出具体类型（INTFLOAT 通常为 float32，不要填 unknown）；
- math_expression 用精准数学伪代码表达核心运算；
- x86_refs 和 arm_refs 应优先填写含实际 SIMD 指令的 .S / .asm 文件（如 sbrdsp.asm、sbrdsp_neon.S）
  路径及函数定义行号，而不是仅填 *_init*.c 的行号—— init.c 只有函数指针赋值，
  .S/.asm 才有可供参考的向量化实现逻辑。

上下文：
{context}
"""



def _number_lines(text: str, max_lines: int = 120) -> str:
    """为文本内容加上行号，便于 LLM 精确定位。"""
    lines = text.splitlines()[:max_lines]
    return "\n".join(f"{i:4d}: {l}" for i, l in enumerate(lines))


def generation_prompt(symbol: str, analysis_json: str, existing_files_map: dict | None = None) -> str:
    existing_section = ""
    if existing_files_map:
        parts = ["\n以下文件在 FFmpeg workspace 中已存在（供参考，勿在 content 字段中输出完整文件）：\n"]
        for path, cnt in existing_files_map.items():
            parts.append(f"--- 已有文件: {path} ---")
            parts.append(cnt[:6000])
            parts.append("--- 文件结束 ---\n")
        existing_section = "\n".join(parts)

    return f"""基于下面的 JSON 分析，为 {symbol} 生成 RVV 代码片段（不要解释）。

★★ 核心原则：输出的每个 item 只包含"新增的片段"，不要输出完整已有文件内容。
   每个 item 的 content 只含本次新增的代码。

要求：
1) .S 汇编实现（target_path 示例：libavcodec/riscv/<module>_rvv.S）
   - 若模块 .S 文件**不存在**：action="create"，content 为完整新 .S 文件
     （含 .text / .align / .globl / .type / label / .size / ret 等）。
   - 若模块 .S 文件**已存在**：action="append"，content 仅含新增函数
     （从 .text 起到最后 .size 结束），不含已有函数。
2) init.c 注册（target_path 示例：libavcodec/riscv/<module>_init.c）
   - action="append"，content 仅含新增的赋值语句（1-3 行），
     如：c_func(ff_xxx) = ff_xxx_rvv;
   - anchor_hint：指出应插入到哪个函数内的哪个位置，如
     "在 ff_sbrdsp_init_riscv() 函数内 #if HAVE_RVV 块末尾"。
3) Makefile（target_path 示例：libavcodec/riscv/Makefile）
   - 仅当创建了新 .S 文件时输出此 item；若只是在已有 .S 追加函数则忽略。
   - action="append"，content 仅含新增的 .o 行（1-2 行），
     如：                        sbrnewfunc_rvv.o \\
   - anchor_hint：如 "追加到 OBJS-$(CONFIG_AAC_DECODER) 块末尾"。

输出格式必须是严格 JSON（不要额外文字）：
{{{{
  "generated": [
    {{{{
      "target_path": "libavcodec/riscv/...",
      "action": "create" | "append",
      "content": "仅新增代码",
      "anchor_hint": "...",
      "description": "一句话说明"
    }}}}
  ]
}}}}
{existing_section}
analysis_json:
{analysis_json}
"""


def injection_locator_prompt(
    target_path: str,
    existing_content: str,
    snippet: str,
    anchor_hint: str,
) -> str:
    return f"""你是代码注入专家。请根据以下信息，输出严格 JSON，指出应将代码片段
插入到目标文件的哪一行之后（0-based 行索引）。

目标文件路径：{target_path}

anchor_hint（生成器提供的插入位置提示）：
{anchor_hint}

待插入代码片段：
```
{snippet[:1000]}
```

目标文件现有内容（含行号）：
{_number_lines(existing_content, max_lines=120)}

输出 JSON schema（不要额外文字）：
{{{{
  "strategy": "insert_after_line" | "append_at_end",
  "line": <0-based 行索引，若 strategy=append_at_end 则填 -1>,
  "reason": "一句话说明"
}}}}

规则：
- 优先依据 anchor_hint 找到最合适的插入位置。
- 若无法确定，使用 "append_at_end"。
- 绝对不要删除或覆盖现有内容。
"""


def plan_prompt(symbol: str) -> str:
    return f"""你是 FFmpeg RVV SIMD 迁移助手。请为迁移算子 {symbol} 生成一份具体的迁移计划。

要求：
- 步骤应具体针对 {symbol}，而不是泛泛的模板
- 必须包含：定位 C 实现、定位参考实现（x86/ARM）、生成 RVV 实现、如何集成到构建系统、运行 checkasm 验证
- 输出严格 JSON（不要额外文字）

输出格式：
{{
  "symbol": "{symbol}",
  "steps": [
    "步骤1",
    "步骤2"
  ],
  "notes": "..."
}}
"""


def plan_refine_prompt(symbol: str, current_steps: list[str], user_feedback: str) -> str:
    steps_s = "\n".join(f"{i+1}. {s}" for i, s in enumerate(current_steps))
    return f"""当前为算子 {symbol} 生成的迁移计划如下：

{steps_s}

用户反馈/修改意见：
{user_feedback}

请根据反馈修改计划，输出严格 JSON（不要额外文字）：
{{
  "symbol": "{symbol}",
  "steps": ["..."],
  "notes": "..."
}}
"""


def files_refine_prompt(symbol: str, current_files: list[str], user_feedback: str) -> str:
    files_s = "\n".join(f"- {f}" for f in current_files)
    return f"""当前为算子 {symbol} 选择的参考文件如下：

{files_s}

用户反馈/修改意见：
{user_feedback}

请根据反馈调整文件列表，输出严格 JSON（不要额外文字）：
{{
  "symbol": "{symbol}",
  "c": ["..."],
  "x86": ["..."],
  "arm": ["..."],
  "riscv": ["..."],
  "headers": ["..."],
  "makefiles": ["..."],
  "checkasm": ["..."],
  "notes": "..."
}}
"""


def build_fix_prompt(symbol: str, build_error: str, generated_files: list[dict]) -> str:
    files_s = ""
    for f in generated_files[:4]:
        path = f.get("path", "?")
        content = f.get("content", "")[:3000]
        files_s += f"\n--- {path} ---\n{content}\n--- end ---\n"

    return f"""构建 {symbol} 时发生编译错误，请根据编译错误信息对相应的代码进行修改。

编译错误信息：
{build_error[:3000]}


请输出修复后的完整文件，格式为严格 JSON（不要额外文字）：
{{
  "files": [{{"path": "...", "content": "..."}}, ...],
  "patches": [{{"path": "...", "diff": "..."}}, ...]
}}
"""

