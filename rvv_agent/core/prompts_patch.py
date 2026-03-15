"""core.prompts_patch — Prompt templates for PATCH and DEBUG stages.

Separated from core/prompts.py to keep the original prompts untouched
(pipeline mode still uses them).
"""
from __future__ import annotations

import json

#NOTE：这个部分是否可以添加skills？关于什么情况可能需要做什么改动，不一定只是append代码，还可能是新建文件。三个prompt要好好斟酌一下
def patch_locate_prompt(
    symbol: str,
    analysis_json: dict,
    selected_files: list[str],
    code_context: str,
) -> str:
    """Prompt for Step 1: locate precise patch points."""
    return f"""你是 FFmpeg RVV 迁移专家。

目标算子: {symbol}

## 语义分析
{json.dumps(analysis_json, ensure_ascii=False, indent=2)}

## 参考文件列表
{json.dumps(selected_files, ensure_ascii=False)}

## 代码上下文
{code_context[:6000]}

## 锚点定位技能
你需要识别以下类型的锚点：
- **函数声明/定义**：C 源文件中的函数签名行，用于确定 RVV 替代目标
- **#include 行**：头文件中需要添加 RVV 函数声明的位置
- **条件编译块**：`#if HAVE_RVV` / `#if HAVE_RV` 块，用于注册 RVV 实现
- **Makefile 规则**：`OBJS-$(CONFIG_...)` 块，用于添加新的 .o 目标
- **汇编文件末尾**：已有 .S 文件的最后一个 `.size` 指令之后，用于追加新函数

不同文件类型的典型插入位置：
- **.S 汇编文件**：在文件末尾（最后一个 .size 之后）追加新函数，或创建新文件
- **init.c 注册文件**：在对应的 `#if HAVE_RVV` 条件块内，已有赋值语句之后
- **Makefile**：在对应的 `OBJS-$(CONFIG_...)` 块末尾追加 .o 目标
- **头文件 (.h)**：在已有的函数声明列表末尾

## 任务
分析上述信息，确定需要修改/创建的文件及精确插入位置。

对每个需要变更的文件，输出:
- file: 相对路径
- line: 插入行号（0-based，-1 表示新建文件或追加到末尾）
- rationale: 为什么在这里插入

严格输出 JSON:
{{"patch_points": [{{"file": "...", "line": -1, "rationale": "..."}}]}}"""


def patch_design_prompt(
    symbol: str,
    analysis_json: dict,
    patch_points: list[dict],
    kb_patterns: list[dict] | None = None,
) -> str:
    """Prompt for Step 2: design the patch (what to change, not the code)."""
    kb_section = ""
    if kb_patterns:
        kb_section = f"\n## 知识库中的相关模式\n{json.dumps(kb_patterns, ensure_ascii=False, indent=2)}"

    return f"""你是 FFmpeg RVV 迁移专家。

目标算子: {symbol}

## 语义分析
{json.dumps(analysis_json, ensure_ascii=False, indent=2)}

## 锚点定位结果
{json.dumps(patch_points, ensure_ascii=False, indent=2)}
{kb_section}

## 变更类型技能
你可以使用以下变更类型，每种类型有不同的语义：

- **create_file**: 创建全新文件
  示例: 创建 `libavcodec/riscv/sbrdsp_rvv.S`（新的 RVV 汇编实现）
- **append_function**: 在已有文件末尾追加新函数
  示例: 在已有的 `sbrdsp_rvv.S` 末尾追加 `ff_sbr_neg_odd_64_rvv` 函数
- **inject_init**: 在 init.c 的条件编译块内注入函数指针赋值
  示例: 在 `ff_sbrdsp_init_riscv()` 的 `#if HAVE_RVV` 块内添加 `c->neg_odd_64 = ff_sbr_neg_odd_64_rvv;`
- **inject_header**: 在头文件中添加函数声明
  示例: 在 `sbrdsp.h` 中添加 `void ff_sbr_neg_odd_64_rvv(INTFLOAT *x, int len);`
- **inject_makefile**: 在 Makefile 的 OBJS 列表中添加 .o 目标
  示例: 在 `OBJS-$(CONFIG_AAC_DECODER)` 块末尾添加 `sbrdsp_rvv.o`

## 任务
基于以上信息，设计变更方案。对每个变更点，说明:
- type: 上述变更类型之一
- file: 目标文件路径
- description: 变更内容描述
- code_items: 需要生成的代码项列表（函数名/宏名/规则名）

同时给出整体 rationale。

严格输出 JSON:
{{"changes": [{{"type": "...", "file": "...", "description": "...", "code_items": [...]}}], "rationale": "..."}}"""


def patch_generate_prompt(
    symbol: str,
    analysis_json: dict,
    design: dict,
    existing_files_map: dict[str, str] | None = None,
    build_errors: str | None = None,
    debug_suggestions: list[str] | None = None,
    previous_code: dict | None = None,
    kb_errors: list[dict] | None = None,
) -> str:
    """Prompt for Step 3: generate actual code based on design.

    When ``build_errors`` is provided (retry after DEBUG), the prompt includes
    the error text, debug suggestions, and the previous failing code so the LLM
    can produce a targeted fix rather than regenerating from scratch.
    """
    existing_section = ""
    if existing_files_map:
        parts = []
        for path, content in existing_files_map.items():
            parts.append(f"### {path}\n```\n{content[:3000]}\n```")
        existing_section = "\n## 现有文件内容（需要做增量合并）\n" + "\n".join(parts)

    kb_section = ""
    if kb_errors:
        kb_parts = []
        for er in kb_errors:
            kb_parts.append(
                f"- [{er.get('error_class', '?')}] {er.get('pattern', '')[:80]} → 修复: {er.get('fix_strategy', '')}"
            )
        kb_section = "\n## 历史错误经验（来自知识库，请避免重复这些错误）\n" + "\n".join(kb_parts) + "\n"

    fix_section = ""
    if build_errors:
        fix_section += f"\n## 上次构建错误（必须修复）\n```\n{build_errors[:4000]}\n```\n"
        if debug_suggestions:
            fix_section += "\n## 诊断建议\n" + "\n".join(f"- {s}" for s in debug_suggestions) + "\n"
        if previous_code:
            prev_parts = []
            for item in previous_code.get("generated", []):
                tp = item.get("target_path", "?")
                code = item.get("content", "")
                prev_parts.append(f"### {tp}\n```\n{code[:3000]}\n```")
            if prev_parts:
                fix_section += "\n## 上次生成的代码（有错误，需要修正）\n" + "\n".join(prev_parts) + "\n"
        fix_section += "\n请根据以上错误信息修正代码，而不是从头重新生成。\n"

    return f"""你是 FFmpeg RVV 迁移专家。请根据变更设计生成完整代码。

目标算子: {symbol}

## 语义分析
{json.dumps(analysis_json, ensure_ascii=False, indent=2)}

## 变更设计
{json.dumps(design, ensure_ascii=False, indent=2)}
{existing_section}{kb_section}{fix_section}

## 要求
1. .S 文件：使用 RISC-V Vector (RVV) 汇编，遵循 FFmpeg 汇编风格
   - 标准循环模板：`vsetvli` 设置向量长度 → `vle/vlse` 加载 → 计算指令 → `vse/vsse` 存储 → 更新指针 → 循环
   - 函数结构：`.text` / `.option arch, +v` / `.globl func_name` / `.type func_name, @function` / `func_name:` / ... / `ret` / `.size func_name, .-func_name`
   - 尾部处理：使用 `vsetvli` 的自然尾部收敛，无需额外 mask（除非算法要求）
   - 注意数据类型宽度：float32 用 `vle32/vse32`，int16 用 `vle16/vse16`，以此类推
2. init.c 文件：注册 RVV 实现到 DSP context
   - 标准模板：`if (flags & AV_CPU_FLAG_RVV_I32) {{ c->func_name = ff_func_name_rvv; }}`
   - 仅注册当前迁移的函数，不要注册尚未实现的函数（避免链接错误）
3. Makefile：添加新文件到编译单元
   - 标准格式：`OBJS-$(CONFIG_XXX) += module_rvv.o`（仅在创建新 .S 文件时需要）
4. 对已有文件做增量修改（不要重写整个文件，只输出需要添加的代码片段）

严格输出 JSON:
{{"generated": [{{"target_path": "...", "action": "create"|"append"|"inject", "content": "...", "anchor_hint": "...", "description": "..."}}]}}"""


def debug_classify_prompt(error_text: str, current_patch: dict | None = None) -> str:
    """Prompt for DEBUG stage: classify error and suggest rollback target."""
    patch_section = ""
    if current_patch:
        patch_section = f"\n## 当前 Patch 信息\n{json.dumps(current_patch, ensure_ascii=False, indent=2)[:3000]}"

    return f"""你是构建错误诊断专家。

## 构建错误
{error_text[:4000]}
{patch_section}

## 任务
1. 将错误分类为: compile_error | link_error | runtime_error | test_mismatch
2. 确定回滚目标:
   - "locate": 锚点漂移或 patch 应用位置错误
   - "design": 构建系统问题（Makefile 未添加文件、缺少头文件包含等）
   - "generate": 代码本身有语法/逻辑错误
3. 给出具体修复建议

严格输出 JSON:
{{"error_class": "...", "rollback_target": "...", "fix_actions": ["..."], "suggestion": "..."}}"""
