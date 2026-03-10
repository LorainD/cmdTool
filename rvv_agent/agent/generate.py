"""agent.generate — 生成 Agent

负责：
  - 迁移计划（Plan / fixed_plan / llm_plan）
  - LLM 语义分析（AnalysisResult / analyze_with_llm）
  - LLM 代码生成与构建修复（GenerationResult / generate_with_llm / fix_generation_with_llm）
  - 生成物落盘（materialize_package）
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..core.config import AppConfig
from ..core.llm import LlmError, LlmMessage, chat_completion
from ..core.prompts import (
    analysis_prompt, build_fix_prompt, generation_prompt, plan_prompt, system_prompt,
)
from ..core.util import ensure_dir, write_json, write_text
from .search import Discovery, build_llm_context, group_files

# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

@dataclass
class Plan:
    steps: list[str]


def fixed_plan(symbol: str) -> Plan:
    """LLM 不可用时的硬编码兜底计划。"""
    return Plan(steps=[
        f"意图解析：迁移 {symbol}",
        "定位 C 实现",
        "定位 x86 / ARM 参考实现",
        "语义抽象（结构化任务描述 JSON）",
        f"调用 LLM 生成 {symbol} 的 RVV asm + init + Makefile patch（落到 runs/）",
        "（可选）把补丁应用到 workspace",
        "（可选）交叉 configure + build checkasm",
        "生成 run 报告（轨迹、输入输出、命令、摘要）",
    ])


def llm_plan(cfg: AppConfig, symbol: str) -> Plan:
    """调用 LLM 生成针对 symbol 的迁移计划，失败时回退到 fixed_plan。"""
    messages = [
        LlmMessage(role="system", content=system_prompt()),
        LlmMessage(role="user", content=plan_prompt(symbol)),
    ]
    try:
        raw = chat_completion(cfg.llm, messages, max_tokens=600, stage="plan")
        raw = raw.strip()
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

# ---------------------------------------------------------------------------
# Analysis Agent
# ---------------------------------------------------------------------------

@dataclass
class AnalysisResult:
    analysis: dict
    raw_text: str
    llm_used: bool
    error: str | None = None


def _fallback_analysis(discovery: Discovery) -> dict:
    g = group_files(discovery)
    return {
        "symbol": discovery.symbol,
        "datatype": "unknown",
        "vectorizable": True,
        "pattern": [],
        "has_stride": False,
        "has_saturation": False,
        "reduction": False,
        "tail_required": False,
        "math_expression": "unknown",
        "c_candidates": [f"{m.file}:{m.line}" for m in discovery.matches if m.file.endswith(".c")][:20],
        "x86_refs": g["x86_refs"],
        "arm_refs": g["arm_refs"],
        "notes": "LLM 未运行或解析失败，使用 fallback。",
    }


def analyze_with_llm(
    cfg: AppConfig,
    discovery: Discovery,
    *,
    context_override: str | None = None,
) -> AnalysisResult:
    ctx = context_override if context_override is not None else build_llm_context(discovery)
    messages = [
        LlmMessage(role="system", content=system_prompt()),
        LlmMessage(role="user", content=analysis_prompt(discovery.symbol, ctx)),
    ]
    try:
        raw = chat_completion(cfg.llm, messages, max_tokens=1400, stage="analyze")
        data = json.loads(raw)
        return AnalysisResult(analysis=data, raw_text=raw, llm_used=True)
    except LlmError as e:
        fb = _fallback_analysis(discovery)
        return AnalysisResult(analysis=fb, raw_text=str(e), llm_used=False, error=str(e))
    except Exception as e:
        fb = _fallback_analysis(discovery)
        return AnalysisResult(analysis=fb, raw_text=repr(e), llm_used=False, error=repr(e))

# ---------------------------------------------------------------------------
# Generate Agent
# ---------------------------------------------------------------------------

@dataclass
class GenerationResult:
    generate_plan: dict
    raw_text: str
    llm_used: bool
    error: str | None = None

    @property
    def package(self) -> dict:
        """Backward-compat: wraps generate_plan as old {files, patches} format."""
        files = [
            {"path": item.get("target_path", ""), "content": item.get("content", "")}
            for item in self.generate_plan.get("generated", [])
        ]
        return {"files": files, "patches": []}



def _fallback_plan(symbol: str) -> dict:
    return {
        "generated": [
            {
                "target_path": f"libavcodec/riscv/{symbol}_rvv.S",
                "action": "create",
                "content": (
                    "/* TODO: auto-generated placeholder (LLM disabled). */\n"
                    ".text\n.align 2\n"
                    f".globl {symbol}\n.type {symbol}, @function\n"
                    f"{symbol}:\n\tret\n"
                ),
                "anchor_hint": "",
                "description": "placeholder - LLM disabled",
            }
        ],
    }




def _extract_gen_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0]
        raw = raw.strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        return json.loads(raw[start : end + 1])
    return json.loads(raw)



def _has_real_rvv_functions(generate_plan: dict) -> bool:
    """Check whether the generate_plan contains a real RVV implementation.

    Supports both new format {generated:[...]} and legacy {files:[...]}.
    """
    PLACEHOLDER_MARKERS = ("TODO: auto-generated placeholder", ".globl\n")
    REAL_MARKERS = (
        "vsetvli", "vle", "vse", "vadd", "vsub", "vmul", "vfadd", "vfsub",
        "vfmul", "vfneg", "vmv", "vmerge", "viota", "vid", "vfnmacc",
        "vxor", "vneg", "vand", "vor", "lb\t", "lbu\t", "lh\t", "lw\t",
    )
    items = generate_plan.get("generated", [])
    if not items:
        items = [
            {"target_path": f.get("path", ""), "content": f.get("content", "")}
            for f in generate_plan.get("files", [])
        ]
    for item in items:
        path = str(item.get("target_path", "") or item.get("path", ""))
        text = str(item.get("content", ""))
        if not path.endswith(".S"):
            continue
        if any(m in text for m in PLACEHOLDER_MARKERS):
            return False
        if any(m in text for m in REAL_MARKERS):
            return True
    return False




def scan_existing_rvv_content(
    ffmpeg_root: Path,
    ref_files: list[str],
    symbol: str = "",
) -> dict[str, str]:
    """读取 LLM 需要做全量合并的现有文件内容（仅 init.c / Makefile，不含 .S）。"""
    existing: dict[str, str] = {}

    def _read(rel: str) -> None:
        full = ffmpeg_root / rel
        if full.exists() and full.is_file():
            try:
                existing[rel] = full.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass

    module_lower = symbol.split(".")[0].lower() if symbol else ""

    for rel in ref_files:
        if Path(rel).suffix == ".S":
            continue
        _read(rel)

    riscv_dir = ffmpeg_root / "libavcodec" / "riscv"    #TODO：不止包含codec
    if riscv_dir.is_dir():
        for f in riscv_dir.iterdir():
            if not f.is_file():
                continue
            rel = str(f.relative_to(ffmpeg_root)).replace("\\", "/")
            if rel in existing:
                continue
            if f.name == "Makefile":
                _read(rel)
            elif "init" in f.name and f.suffix == ".c":
                if not module_lower or module_lower in f.name.lower():
                    _read(rel)
    return existing



def generate_with_llm(
    cfg: AppConfig,
    symbol: str,
    analysis_json: dict,
    *,
    existing_files_map: dict[str, str] | None = None,
) -> GenerationResult:
    messages = [
        LlmMessage(role="system", content=system_prompt()),
        LlmMessage(
            role="user",
            content=generation_prompt(
                symbol,
                json.dumps(analysis_json, ensure_ascii=False),
                existing_files_map=existing_files_map,
            ),
        ),
    ]
    try:
        raw = chat_completion(cfg.llm, messages, max_tokens=2800, stage="generate")
        data = _extract_gen_json(raw)
        # Normalize legacy {files:[...]} format to new {generated:[...]}
        if "files" in data and "generated" not in data:
            data = {
                "generated": [
                    {
                        "target_path": f.get("path", ""),
                        "action": "create",
                        "content": f.get("content", ""),
                        "anchor_hint": "",
                        "description": "",
                    }
                    for f in data.get("files", [])
                ]
            }
        return GenerationResult(generate_plan=data, raw_text=raw, llm_used=True)
    except LlmError as e:
        return GenerationResult(generate_plan=_fallback_plan(symbol), raw_text=str(e), llm_used=False, error=str(e))
    except Exception as e:
        return GenerationResult(generate_plan=_fallback_plan(symbol), raw_text=repr(e), llm_used=False, error=repr(e))



def fix_generation_with_llm(
    cfg: AppConfig,
    symbol: str,
    build_error: str,
    current_plan: dict,
) -> GenerationResult:
    """Call LLM to fix a failed build; accepts new generate_plan format."""
    files_for_prompt = [
        {"path": item.get("target_path", ""), "content": item.get("content", "")}
        for item in current_plan.get("generated", [])
    ]
    messages = [
        LlmMessage(role="system", content=system_prompt()),
        LlmMessage(role="user", content=build_fix_prompt(symbol, build_error, files_for_prompt)),
    ]
    try:
        raw = chat_completion(cfg.llm, messages, max_tokens=2800, stage="fix")
        data = _extract_gen_json(raw)
        if "files" in data and "generated" not in data:
            data = {
                "generated": [
                    {
                        "target_path": f.get("path", ""),
                        "action": "create",
                        "content": f.get("content", ""),
                        "anchor_hint": "",
                        "description": "(fix attempt)",
                    }
                    for f in data.get("files", [])
                ]
            }
        return GenerationResult(generate_plan=data, raw_text=raw, llm_used=True)
    except Exception as e:
        return GenerationResult(generate_plan=current_plan, raw_text=repr(e), llm_used=False, error=repr(e))


def materialize_package(
    run_dir: Path,
    ffmpeg_root: Path,
    package: dict,
    *,
    apply: bool,
    attempt: int = 0,
) -> list[Path]:
    out_paths: list[Path] = []
    attempt_suffix = f"_attempt{attempt}" if attempt > 0 else ""
    artifacts_dir = run_dir / f"artifacts{attempt_suffix}"
    ensure_dir(artifacts_dir)
    write_json(artifacts_dir / "package.json", package)

    for f in package.get("files", []):
        rel = Path(str(f.get("path", "")))
        content = str(f.get("content", ""))
        if not rel.as_posix() or not content:
            continue
        dst_trace = artifacts_dir / "files" / rel
        write_text(dst_trace, content)
        out_paths.append(dst_trace)
        if apply:
            dst = ffmpeg_root / rel
            ensure_dir(dst.parent)
            # .S 文件：追加新函数而非覆盖，保护已有 RVV 实现
            if rel.suffix == ".S" and dst.exists():
                existing_content = dst.read_text(encoding="utf-8", errors="replace")
                if content.strip() and content.strip() not in existing_content:
                    merged = existing_content.rstrip("\n") + "\n\n" + content.lstrip("\n")
                    write_text(dst, merged)
                    out_paths.append(dst)
            else:
                write_text(dst, content)
                out_paths.append(dst)

    for patch in package.get("patches", []):
        rel_patch = str(patch.get("path", ""))
        diff = str(patch.get("diff", ""))
        if not rel_patch or not diff:
            continue
        p_name = Path(rel_patch).name
        p_path = artifacts_dir / "patches" / p_name
        write_text(p_path.with_suffix(p_path.suffix + ".diff"), diff)
        out_paths.append(p_path.with_suffix(p_path.suffix + ".diff"))
        if apply:
            target = ffmpeg_root / rel_patch
            ensure_dir(target.parent)
            diff_file = p_path.with_suffix(p_path.suffix + ".diff").resolve()
            result = subprocess.run(
                ["patch", "-p1", "--forward", "--reject-file=-", "-i", str(diff_file)],
                cwd=str(ffmpeg_root), capture_output=True, text=True,
            )
            if result.returncode == 0:
                out_paths.append(target)
            else:
                write_text(
                    artifacts_dir / "patches" / (p_name + ".patch_error.txt"),
                    result.stdout + result.stderr,
                )
    return out_paths


def save_generate_folder(
    run_dir: Path,
    generate_plan: dict,
    attempt: int = 0,
) -> Path:
    """Save generate_plan to run_dir/generate[_fixN]/ for debugging.

    Layout:
        generate/
            plan.json       -- full plan JSON
            files/
                <basename>  -- individual file contents
    """
    suffix = f"_fix{attempt}" if attempt > 0 else ""
    gen_dir = run_dir / f"generate{suffix}"
    ensure_dir(gen_dir)
    write_json(gen_dir / "plan.json", generate_plan)
    files_dir = gen_dir / "files"
    ensure_dir(files_dir)
    for item in generate_plan.get("generated", []):
        rel = Path(str(item.get("target_path", ""))).name
        txt = str(item.get("content", ""))
        if rel and txt:
            write_text(files_dir / rel, txt)
    return gen_dir


# ---------------------------------------------------------------------------
# Context-aware stage wrappers
# ---------------------------------------------------------------------------

def analyze(ctx: "MigrationContext") -> "MigrationContext":
    """Context-aware analysis stage.

    Calls :func:`analyze_with_llm` using ``ctx.discovery`` and stores
    the result back into *ctx*.

    Updates
    -------
    ``ctx.analysis_result`` — full :class:`AnalysisResult`.
    """
    result = analyze_with_llm(ctx.cfg, ctx.discovery)
    ctx.analysis_result = result
    return ctx


def generate(ctx: "MigrationContext") -> "MigrationContext":
    """Context-aware generation stage.

    Calls :func:`generate_with_llm` using the analysis stored in *ctx* and
    saves generated files to ``ctx.run_dir/generate/``.

    Updates
    -------
    ``ctx.current_gen`` — :class:`GenerationResult` from the LLM.
    """
    analysis_text = ctx.analysis_result.analysis if ctx.analysis_result else ""
    gen = generate_with_llm(ctx.cfg, ctx.operator, analysis_text)
    ctx.current_gen = gen
    if ctx.run_dir is not None:
        save_generate_folder(ctx.run_dir, gen.generate_plan)
    return ctx
