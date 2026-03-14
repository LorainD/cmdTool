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

## 任务
基于以上信息，设计变更方案。对每个变更点，说明:
- type: "create_file" | "append_function" | "inject_code" | "add_build_rule"
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
{existing_section}{fix_section}

## 要求
1. .S 文件：使用 RISC-V Vector (RVV) 汇编，遵循 FFmpeg 汇编风格
2. init.c 文件：注册 RVV 实现到 DSP context
3. Makefile：添加新文件到编译单元
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
