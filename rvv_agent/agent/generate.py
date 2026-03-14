"""agent.generate — 生成 Agent (DEPRECATED)

本文件中的所有业务函数已被状态机架构替代：
  - Plan → agent.plan
  - Analysis → agent.analyze
  - Code generation → patch.py::generate_code
  - Build fix → patch.py::generate_code (retry mode)
  - Materialize → patch.py::apply_patch

保留此文件仅用于：
  1. Re-export Plan/AnalysisResult 等类型，维持向后兼容
  2. GenerationResult dataclass 定义（仍被 debug.py 遗留代码类型引用）
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..core.config import AppConfig
from ..core.llm import LlmError, LlmMessage, chat_completion
from ..core.prompts import build_fix_prompt, generation_prompt, system_prompt
from ..core.util import ensure_dir, write_json, write_text

# Re-export for backward compatibility
from .analyze import AnalysisResult, analyze_with_llm
from .plan import Plan, fixed_plan, llm_plan

# ---------------------------------------------------------------------------
# GenerationResult dataclass (仍被 __init__.py re-export)
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


# ---------------------------------------------------------------------------
# 以下所有函数已被 patch.py 中的对应实现替代，注释保留用于参考
# ---------------------------------------------------------------------------

# def _fallback_plan(symbol: str) -> dict:
#     return {
#         "generated": [
#             {
#                 "target_path": f"libavcodec/riscv/{symbol}_rvv.S",
#                 "action": "create",
#                 "content": (
#                     "/* TODO: auto-generated placeholder (LLM disabled). */\n"
#                     ".text\n.align 2\n"
#                     f".globl {symbol}\n.type {symbol}, @function\n"
#                     f"{symbol}:\n\tret\n"
#                 ),
#                 "anchor_hint": "",
#                 "description": "placeholder - LLM disabled",
#             }
#         ],
#     }


# def _extract_gen_json(raw: str) -> dict:
#     """DEPRECATED: 使用 patch.py::_extract_gen_json 替代。"""
#     raw = raw.strip()
#     if raw.startswith("```"):
#         raw = raw.split("\n", 1)[-1]
#         if raw.endswith("```"):
#             raw = raw.rsplit("```", 1)[0]
#         raw = raw.strip()
#     start = raw.find("{")
#     end = raw.rfind("}")
#     if start != -1 and end > start:
#         return json.loads(raw[start : end + 1])
#     return json.loads(raw)


# def _has_real_rvv_functions(generate_plan: dict) -> bool:
#     """Check whether the generate_plan contains a real RVV implementation.
#
#     DEPRECATED: 功能已内联到 chat.py::handle_build 的校验逻辑中。
#     """
#     PLACEHOLDER_MARKERS = ("TODO: auto-generated placeholder", ".globl\n")
#     REAL_MARKERS = (
#         "vsetvli", "vle", "vse", "vadd", "vsub", "vmul", "vfadd", "vfsub",
#         "vfmul", "vfneg", "vmv", "vmerge", "viota", "vid", "vfnmacc",
#         "vxor", "vneg", "vand", "vor", "lb\t", "lbu\t", "lh\t", "lw\t",
#     )
#     items = generate_plan.get("generated", [])
#     if not items:
#         items = [
#             {"target_path": f.get("path", ""), "content": f.get("content", "")}
#             for f in generate_plan.get("files", [])
#         ]
#     for item in items:
#         path = str(item.get("target_path", "") or item.get("path", ""))
#         text = str(item.get("content", ""))
#         if not path.endswith(".S"):
#             continue
#         if any(m in text for m in PLACEHOLDER_MARKERS):
#             return False
#         if any(m in text for m in REAL_MARKERS):
#             return True
#     return False


# NOTE: 以下函数在新的状态机架构中已不再使用，保留用于向后兼容
# 实际使用的是 patch.py 中的对应功能

# def scan_existing_rvv_content(
#     ffmpeg_root: Path,
#     ref_files: list[str],
#     symbol: str = "",
# ) -> dict[str, str]:
#     """读取 LLM 需要做全量合并的现有文件内容（仅 init.c / Makefile，不含 .S）。
#
#     DEPRECATED: 已被 patch.py::generate_code 中的 existing_map 构建逻辑替代。
#     """
#     existing: dict[str, str] = {}
#
#     def _read(rel: str) -> None:
#         full = ffmpeg_root / rel
#         if full.exists() and full.is_file():
#             try:
#                 existing[rel] = full.read_text(encoding="utf-8", errors="replace")
#             except Exception:
#                 pass
#
#     module_lower = symbol.split(".")[0].lower() if symbol else ""
#
#     for rel in ref_files:
#         if Path(rel).suffix == ".S":
#             continue
#         _read(rel)
#
#     # TODO：不止包含codec，应该扫描所有 libav* 目录下的 riscv 子目录
#     riscv_dir = ffmpeg_root / "libavcodec" / "riscv"
#     if riscv_dir.is_dir():
#         for f in riscv_dir.iterdir():
#             if not f.is_file():
#                 continue
#             rel = str(f.relative_to(ffmpeg_root)).replace("\\", "/")
#             if rel in existing:
#                 continue
#             if f.name == "Makefile":
#                 _read(rel)
#             elif "init" in f.name and f.suffix == ".c":
#                 if not module_lower or module_lower in f.name.lower():
#                     _read(rel)
#     return existing


# def generate_with_llm(
#     cfg: AppConfig,
#     symbol: str,
#     analysis_json: dict,
#     *,
#     existing_files_map: dict[str, str] | None = None,
# ) -> GenerationResult:
#     """DEPRECATED: 已被 patch.py::generate_code 替代。"""
#     messages = [
#         LlmMessage(role="system", content=system_prompt()),
#         LlmMessage(
#             role="user",
#             content=generation_prompt(
#                 symbol,
#                 json.dumps(analysis_json, ensure_ascii=False),
#                 existing_files_map=existing_files_map,
#             ),
#         ),
#     ]
#     try:
#         raw = chat_completion(cfg.llm, messages, max_tokens=2800, stage="generate")
#         data = _extract_gen_json(raw)
#         # Normalize legacy {files:[...]} format to new {generated:[...]}
#         if "files" in data and "generated" not in data:
#             data = {
#                 "generated": [
#                     {
#                         "target_path": f.get("path", ""),
#                         "action": "create",
#                         "content": f.get("content", ""),
#                         "anchor_hint": "",
#                         "description": "",
#                     }
#                     for f in data.get("files", [])
#                 ]
#             }
#         return GenerationResult(generate_plan=data, raw_text=raw, llm_used=True)
#     except LlmError as e:
#         return GenerationResult(generate_plan=_fallback_plan(symbol), raw_text=str(e), llm_used=False, error=str(e))
#     except Exception as e:
#         return GenerationResult(generate_plan=_fallback_plan(symbol), raw_text=repr(e), llm_used=False, error=repr(e))


# def fix_generation_with_llm(
#     cfg: AppConfig,
#     symbol: str,
#     build_error: str,
#     current_plan: dict,
#     *,
#     analysis: dict | None = None,
#     all_prior_errors: str | None = None,
# ) -> GenerationResult:
#     """Call LLM to fix a failed build; accepts new generate_plan format.
#
#     DEPRECATED: 已被 patch.py::generate_code (retry mode) 替代。
#
#     Args:
#         cfg: 应用配置。
#         symbol: 算子名。
#         build_error: 当前这次构建的错误文本。
#         current_plan: 当前生成计划（包含已有文件内容）。
#         analysis: 完整的算子语义分析 JSON（从 DynamicContext 传入，不可省略）。
#         all_prior_errors: 所有历次构建错误的汇总文本（帮助 LLM 避免重复错误）。
#     """
#     files_for_prompt = [
#         {"path": item.get("target_path", ""), "content": item.get("content", "")}
#         for item in current_plan.get("generated", [])
#     ]
#     messages = [
#         LlmMessage(role="system", content=system_prompt()),
#         LlmMessage(
#             role="user",
#             content=build_fix_prompt(
#                 symbol,
#                 build_error,
#                 files_for_prompt,
#                 analysis=analysis,
#                 all_prior_errors=all_prior_errors,
#             ),
#         ),
#     ]
#     try:
#         raw = chat_completion(cfg.llm, messages, max_tokens=2800, stage="fix")
#         data = _extract_gen_json(raw)
#         if "files" in data and "generated" not in data:
#             data = {
#                 "generated": [
#                     {
#                         "target_path": f.get("path", ""),
#                         "action": "create",
#                         "content": f.get("content", ""),
#                         "anchor_hint": "",
#                         "description": "(fix attempt)",
#                     }
#                     for f in data.get("files", [])
#                 ]
#             }
#         return GenerationResult(generate_plan=data, raw_text=raw, llm_used=True)
#     except Exception as e:
#         return GenerationResult(generate_plan=current_plan, raw_text=repr(e), llm_used=False, error=repr(e))

# NOTE: 以下函数在新的状态机架构中已不再使用，保留用于向后兼容
# 实际使用的是 patch.py 中的对应功能

# def materialize_package(
#     run_dir: Path,
#     ffmpeg_root: Path,
#     package: dict,
#     *,
#     apply: bool,
#     attempt: int = 0,
# ) -> list[Path]:
#     """DEPRECATED: 已被 patch.py::apply_patch 替代。"""
#     out_paths: list[Path] = []
#     attempt_suffix = f"_attempt{attempt}" if attempt > 0 else ""
#     artifacts_dir = run_dir / f"artifacts{attempt_suffix}"
#     ensure_dir(artifacts_dir)
#     write_json(artifacts_dir / "package.json", package)
#
#     for f in package.get("files", []):
#         rel = Path(str(f.get("path", "")))
#         content = str(f.get("content", ""))
#         if not rel.as_posix() or not content:
#             continue
#         dst_trace = artifacts_dir / "files" / rel
#         write_text(dst_trace, content)
#         out_paths.append(dst_trace)
#         if apply:
#             dst = ffmpeg_root / rel
#             ensure_dir(dst.parent)
#             # .S 文件：追加新函数而非覆盖，保护已有 RVV 实现
#             if rel.suffix == ".S" and dst.exists():
#                 existing_content = dst.read_text(encoding="utf-8", errors="replace")
#                 if content.strip() and content.strip() not in existing_content:
#                     merged = existing_content.rstrip("\n") + "\n\n" + content.lstrip("\n")
#                     write_text(dst, merged)
#                     out_paths.append(dst)
#             else:
#                 write_text(dst, content)
#                 out_paths.append(dst)
#
#     for patch in package.get("patches", []):
#         rel_patch = str(patch.get("path", ""))
#         diff = str(patch.get("diff", ""))
#         if not rel_patch or not diff:
#             continue
#         p_name = Path(rel_patch).name
#         p_path = artifacts_dir / "patches" / p_name
#         write_text(p_path.with_suffix(p_path.suffix + ".diff"), diff)
#         out_paths.append(p_path.with_suffix(p_path.suffix + ".diff"))
#         if apply:
#             target = ffmpeg_root / rel_patch
#             ensure_dir(target.parent)
#             diff_file = p_path.with_suffix(p_path.suffix + ".diff").resolve()
#             result = subprocess.run(
#                 ["patch", "-p1", "--forward", "--reject-file=-", "-i", str(diff_file)],
#                 cwd=str(ffmpeg_root), capture_output=True, text=True,
#             )
#             if result.returncode == 0:
#                 out_paths.append(target)
#             else:
#                 write_text(
#                     artifacts_dir / "patches" / (p_name + ".patch_error.txt"),
#                     result.stdout + result.stderr,
#                 )
#     return out_paths


# def save_generate_folder(
#     run_dir: Path,
#     generate_plan: dict,
#     attempt: int = 0,
# ) -> Path:
#     """Save generate_plan to run_dir/generate[_fixN]/ for debugging.
#
#     DEPRECATED: 已被 patch.py 中的 artifact 持久化机制替代。
#
#     Layout:
#         generate/
#             plan.json       -- full plan JSON
#             files/
#                 <basename>  -- individual file contents
#     """
#     suffix = f"_fix{attempt}" if attempt > 0 else ""
#     gen_dir = run_dir / f"generate{suffix}"
#     ensure_dir(gen_dir)
#     write_json(gen_dir / "plan.json", generate_plan)
#     files_dir = gen_dir / "files"
#     ensure_dir(files_dir)
#     for item in generate_plan.get("generated", []):
#         rel = Path(str(item.get("target_path", ""))).name
#         txt = str(item.get("content", ""))
#         if rel and txt:
#             write_text(files_dir / rel, txt)
#     return gen_dir


# ---------------------------------------------------------------------------
# Context-aware stage wrappers (DEPRECATED)
# ---------------------------------------------------------------------------
# NOTE: 以下函数为旧 pipeline 模式的上下文包装器，已被状态机架构替代

# def analyze(ctx: "MigrationContext") -> "MigrationContext":
#     """Context-aware analysis stage.
#
#     .. deprecated::
#         Pipeline now uses state-machine handlers. Kept for backward compat.
#
#     Updates
#     -------
#     ``ctx.analysis_result`` — full :class:`AnalysisResult`.
#     """
#     result = analyze_with_llm(ctx.cfg, ctx.discovery)
#     ctx.analysis_result = result
#     return ctx


# def generate(ctx: "MigrationContext") -> "MigrationContext":
#     """Context-aware generation stage.
#
#     .. deprecated::
#         Pipeline now uses state-machine handlers. Kept for backward compat.
#
#     Updates
#     -------
#     ``ctx.current_gen`` — :class:`GenerationResult` from the LLM.
#     """
#     analysis_text = ctx.analysis_result.analysis if ctx.analysis_result else ""
#     gen = generate_with_llm(ctx.cfg, ctx.operator, analysis_text)
#     ctx.current_gen = gen
#     if ctx.run_dir is not None:
#         save_generate_folder(ctx.run_dir, gen.generate_plan)
#     return ctx
