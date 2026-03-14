"""agent.chat — Interactive chat mode (state-machine driven).

Refactored from the original monolithic run_chat() into a state-machine
architecture.  Each migration stage is an independent handler function.
Normal chat (non-migrate) still uses a simple multi-turn conversation loop.

State machine flow:
  INTENT → RETRIEVE → ANALYZE → PLAN → PATCH → BUILD → (TEST) → (KB_UPDATE) → DONE
  BUILD failure → DEBUG → PATCH (retry)
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..core.config import AppConfig
from ..core.llm import (
    LlmMessage,
    chat_completion,
    get_trajectory_dict,
    probe_llm,
    record_trajectory_action,
    reset_trajectory,
)
from ..core.prompts import (
    files_refine_prompt,
    plan_refine_prompt,
    system_prompt,
)
from ..core.statemachine import StateMachine
from ..core.task import (
    AnalysisArtifact,
    BuildArtifact,
    KBUpdateArtifact,
    MigrationTarget,
    PlanArtifact,
    RetrievalArtifact,
    TaskContext,
    TaskState,
)
from ..core.util import (
    ensure_dir,
    extract_build_errors,
    fmt_argv,
    now_id,
    print_llm_error,
    print_red,
    print_yellow,
    slug,
    write_json,
    write_text,
)
from ..memory.knowledge_base import KnowledgeBase, Pattern, ErrorRecord
from ..tool.interactive import prompt_secret, prompt_text, prompt_yes_no


# ---------------------------------------------------------------------------
# LLM probe helper
# ---------------------------------------------------------------------------

def _print_llm_probe(cfg: AppConfig) -> None:
    st = probe_llm(cfg.llm)
    print("LLM status:")
    print(f"- endpoint_url: {st.get('endpoint_url')}")
    print(f"- model: {st.get('model')}")
    print(f"- api_key_present: {st.get('api_key_present')}")
    print(f"- probe_ok: {st.get('probe_ok')}")
    if st.get("probe_ok"):
        print(f"- probe_reply: {st.get('probe_reply')}")
    else:
        print(f"- probe_error: {st.get('probe_error')}")


def _prompt_symbol() -> str:
    """Ask user for a symbol name interactively."""
    follow = prompt_text(
        "请直接输入要迁移的算子/函数名（或输入 /cancel 取消）： "
    ).strip()
    if follow.lower() in {"/cancel", "/c"}:
        return ""
    m = re.search(r"([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)", follow)
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# State handlers
# ---------------------------------------------------------------------------

def handle_intent(task: TaskContext) -> TaskContext:
    """INTENT handler: confirm symbol, persist intent."""
    symbol = task.target.symbol
    if not symbol:
        symbol = _prompt_symbol()
        if not symbol:
            print("未提供有效 symbol，取消迁移。")
            task.current_state = TaskState.DONE
            return task
        task.target.symbol = symbol
        if "." in symbol:
            task.target.module = symbol.split(".")[0]
        else:
            task.target.module = symbol

    print(f"\n迁移目标: {task.target.symbol} (module: {task.target.module})")
    task.save_artifact("INTENT", {
        "module": task.target.module,
        "symbol": task.target.symbol,
        "functions": task.target.functions,
    })
    record_trajectory_action("intent", f"Target confirmed: {task.target.symbol}")
    task.current_state = TaskState.RETRIEVE
    return task


def handle_retrieve(task: TaskContext) -> TaskContext:
    """RETRIEVE handler: search symbol + select reference files + user refinement."""
    from .search import build_context_from_files, select_references
    from .intent import Intent

    ffmpeg_root = task.ffmpeg_root
    symbol = task.target.symbol
#TODO: 这里应该不止是symbol，先由module开始检索
    # Search and select references
    retrieval = select_references(task.cfg, ffmpeg_root, symbol)
    write_text(task.run_dir / "retrieval_raw.txt", retrieval.raw_text + "\n")
    selected = retrieval.selected

    def _list(key: str) -> list[str]:
        v = selected.get(key, [])
        return [str(x) for x in v] if isinstance(v, list) else []

    selected_files: list[str] = []
    for k in ("c", "x86", "arm", "riscv", "headers", "makefiles", "checkasm"):
        selected_files.extend(_list(k))
    selected_files = list(dict.fromkeys(selected_files))

    # Merge existing RVV files
    for r in retrieval.existing_rvv:
        if r not in selected_files:
            selected_files.append(r)

    print("\n检索/选择出的参考文件：")
    for p in selected_files:
        tag = "[existing-rvv] " if p in retrieval.existing_rvv else ""
        print(f"  {tag}{p}")
#TODO：这里检索出的existing-rvv似乎在实际使用时并没有出现？并且，如果只供输出，对后续inject时没有参考意义，似乎冗余了。

    # User refinement
    if not prompt_yes_no("\n确认进入分析/生成阶段？", default=True):
        selected_files = _refine_files(task.cfg, symbol, selected_files)
        if not selected_files:
            print("已取消，本轮结束。")
            task.current_state = TaskState.DONE
            return task

    record_trajectory_action(
        "select_refs",
        f"Reference files confirmed ({len(selected_files)} files)",
        detail="\n".join(selected_files),
        event_type="human_output",
    )

    # Build code context from selected files
    ctx_text = build_context_from_files(
        ffmpeg_root, symbol=symbol, files=selected_files,
    )
    write_text(task.run_dir / "context.txt", ctx_text)

    # Persist artifact
    artifact = RetrievalArtifact(
        discovery_json={
            "symbol": retrieval.discovery.symbol,
            "matches": [m.__dict__ for m in retrieval.discovery.matches[:200]],
        },
        selected_files=selected_files,
        code_context=ctx_text,
    )
    aid = task.save_artifact("RETRIEVE", artifact)
    task.artifacts.retrieval_id = aid

    task.current_state = TaskState.ANALYZE
    return task

#TODO：这个部分的流程应该是根据retrieval和MigrationTarget中，首先在参考文件中找到symbol的源文件，判断其是否有多个function，并更新MigrationTarget中的function。
#TODO：analyze应该分为两个，第一个只做function的判定，在plan之前；第二个做针对function的分析，在plan之后，做语义分析。
def handle_analyze(task: TaskContext) -> TaskContext:
    """ANALYZE handler: LLM semantic analysis → migration contract."""
    from .generate import analyze_with_llm
    from .search import Discovery, Match

    retrieval = task.load_artifact("RETRIEVE")
    symbol = task.target.symbol

    # Reconstruct Discovery for analyze_with_llm
    matches = [Match(**m) for m in retrieval.get("discovery_json", {}).get("matches", [])]
    discovery = Discovery(symbol=symbol, matches=matches)
#TODO：为什么要重新构建matches和discovery？不就是retrieval的结果吗？
#TODO：关于function的分析在哪里？analyze应该是针对function的

    print("\n正在分析算子实现…")
    # Pass accumulated build errors if any (from DEBUG cycles)
    build_errors_text = "\n---\n".join(task.all_build_errors) if task.all_build_errors else None

    analysis = analyze_with_llm(
        task.cfg,
        discovery,
        context_override=retrieval.get("code_context", ""),
        build_errors=build_errors_text,
    )

    record_trajectory_action(
        "analyze",
        f"Analysis complete (llm_used={analysis.llm_used})",
        detail=analysis.raw_text[:2000],
        event_type="human_output",
    )

    # Persist
    artifact = AnalysisArtifact(
        analysis_json=analysis.analysis,
        raw_text=analysis.raw_text,
        llm_used=analysis.llm_used,
    )
    aid = task.save_artifact("ANALYZE", artifact)
    task.artifacts.analysis_ids.append(aid)
    write_json(task.run_dir / "analysis.json", analysis.analysis)

    task.current_state = TaskState.PLAN
    return task




def _refine_plan(cfg: AppConfig, symbol: str, steps: list[str],
                 history: list[dict] | None = None) -> list[str]:
    """Interactive plan refinement loop."""
    while True:
        feedback = prompt_text(
            "请描述修改意见（直接回车接受，输入 /skip 跳过）：\n> "
        ).strip()
        if not feedback:
            return steps
        if feedback.lower() in {"/skip", "/cancel"}:
            return []
        try:
            raw = chat_completion(
                cfg.llm,
                [
                    LlmMessage(role="system", content=system_prompt()),
                    LlmMessage(role="user", content=plan_refine_prompt(symbol, steps, feedback)),
                ],
                max_tokens=600,
            )
            raw = raw.strip()
            start, end = raw.find("{"), raw.rfind("}")
            if start != -1 and end > start:
                data = json.loads(raw[start: end + 1])
                new_steps = [str(s).strip() for s in data.get("steps", []) if str(s).strip()]
                if new_steps:
                    steps = new_steps
                    if history is not None:
                        history.append({"stage": "plan", "feedback": feedback})
        except Exception as e:
            print(f"（LLM refine 失败：{e}，保留当前计划）")

        print("\n修改后的 Plan：")
        for i, s in enumerate(steps, 1):
            print(f"{i:02d}. {s}")
        if prompt_yes_no("\n确认这份计划？", default=True):
            return steps


def _refine_files(cfg: AppConfig, symbol: str, files: list[str],
                  history: list[dict] | None = None) -> list[str]:
    """Interactive reference file list refinement loop."""
    while True:
        feedback = prompt_text(
            "请描述修改意见（直接回车接受，输入 /skip 跳过）：\n> "
        ).strip()
        if not feedback:
            return files
        if feedback.lower() in {"/skip", "/cancel"}:
            return []
        try:
            raw = chat_completion(
                cfg.llm,
                [
                    LlmMessage(role="system", content=system_prompt()),
                    LlmMessage(role="user", content=files_refine_prompt(symbol, files, feedback)),
                ],
                max_tokens=600,
            )
            raw = raw.strip()
            start, end = raw.find("{"), raw.rfind("}")
            if start != -1 and end > start:
                data = json.loads(raw[start: end + 1])
                new_files: list[str] = []
                for k in ("c", "x86", "arm", "riscv", "headers", "makefiles", "checkasm"):
                    v = data.get(k, [])
                    if isinstance(v, list):
                        new_files.extend(str(x) for x in v)
                new_files = list(dict.fromkeys(new_files))
                if new_files:
                    files = new_files
                    if history is not None:
                        history.append({"stage": "files", "feedback": feedback})
        except Exception as e:
            print(f"（LLM refine 失败：{e}，保留当前文件列表）")

        print("\n修改后的参考文件：")
        for f in files:
            print(f"  - {f}")
        if prompt_yes_no("\n确认这份文件列表？", default=True):
            return files

#TODO：添加多函数的迁移顺序，并让agent能实际按照plan定义的函数迁移顺序来执行函数迁移
def handle_plan(task: TaskContext, kb: KnowledgeBase | None = None) -> TaskContext:
    """PLAN handler: generate + refine migration plan."""
    from .generate import llm_plan

    symbol = task.target.symbol

    print("\n正在生成迁移计划…")
    plan = llm_plan(task.cfg, symbol)
    plan_steps = plan.steps

    print("\nPlan：")
    for i, s in enumerate(plan_steps, 1):
        print(f"  {i}. {s}")

    refine_history: list[dict] = []
    if not prompt_yes_no("\n确认按该 plan 继续？", default=True):
        plan_steps = _refine_plan(task.cfg, symbol, plan_steps, history=refine_history)
        if not plan_steps:
            print("已取消，本轮结束。")
            task.current_state = TaskState.DONE
            return task

    record_trajectory_action(
        "plan", f"Plan confirmed for {symbol}",
        detail="\n".join(plan_steps), event_type="human_output",
    )

    # Persist
    artifact = PlanArtifact(
        steps=plan_steps,
        function_order=[symbol],
        acceptance_criteria={"build_ok": True},
        refine_history=refine_history,
    )
    aid = task.save_artifact("PLAN", artifact)
    task.artifacts.plan_id = aid

    task.current_state = TaskState.PATCH
    return task


def handle_patch(task: TaskContext, kb: KnowledgeBase | None = None) -> TaskContext:
    """PATCH handler: delegates to the 4-step patch module."""
    from .patch import run_patch_stage

    kb_patterns = None
    if kb:
        found = kb.search_patterns(symbol=task.target.symbol, max_results=3)    #TODO：后续需要改进，根据类似的pattern模式来检索（rag？）
        if found:
            from dataclasses import asdict
            kb_patterns = [asdict(p) for p in found]

    return run_patch_stage(task, kb_patterns=kb_patterns)




def handle_build(task: TaskContext) -> TaskContext:
    """BUILD handler: configure + make checkasm."""
    from ..tool.exec import (
        ExecResult,
        configure_argv,
        make_checkasm_argv,
        run_configure,
        run_make_checkasm,
    )

    ffmpeg_root = task.ffmpeg_root
    build_dir = ffmpeg_root / task.cfg.ffmpeg.build_dir
    jobs = max(1, os.cpu_count() or 1)

    # Check human policy
    exec_ok = task.cfg.human.exec_ok
    if exec_ok is None:
        print("\n交叉编译计划：")
        print(f"  build 目录 : {build_dir}")
        print(f"  configure  : {fmt_argv(configure_argv(task.cfg, ffmpeg_root))}")
        print(f"  make       : {fmt_argv(make_checkasm_argv(jobs=jobs))}")
        exec_ok = prompt_yes_no("\n是否现在执行 configure + 构建 checkasm？")
        task.cfg.human.exec_ok = exec_ok

    if not exec_ok:
        print("跳过构建阶段。")
        task.current_state = TaskState.DONE
        return task

    ensure_dir(build_dir)

    # --- configure ---
    print(f"\n正在运行 configure…")
    cfg_result = run_configure(task.cfg, ffmpeg_root, build_dir)

    build_artifact = BuildArtifact(
        run_id=now_id(),
        cmd=fmt_argv(configure_argv(task.cfg, ffmpeg_root)),
        stdout=cfg_result.stdout,
        stderr=cfg_result.stderr,
        exitcode=cfg_result.returncode,
        phase="configure",
    )

    if cfg_result.returncode != 0:
        print(f"\nconfigure 失败 (rc={cfg_result.returncode})")
        # Save build log for debugging
        error_extract = extract_build_errors(cfg_result.stdout + cfg_result.stderr)
        write_text(task.run_dir / "build_log.txt",
                   f"=== configure (rc={cfg_result.returncode}) ===\n{error_extract}\n")
        aid = task.save_artifact("BUILD", build_artifact, sub_id=build_artifact.run_id)
        task.artifacts.build_run_ids.append(build_artifact.run_id)
        task.current_state = TaskState.DEBUG
        return task

    # --- make checkasm ---
    print("\nconfigure 完成，开始构建 checkasm…")
    make_result = run_make_checkasm(task.cfg, build_dir, jobs)

    build_artifact = BuildArtifact(
        run_id=now_id(),
        cmd=fmt_argv(make_checkasm_argv(jobs=jobs)),
        stdout=make_result.stdout,
        stderr=make_result.stderr,
        exitcode=make_result.returncode,
        phase="make",
        artifact_path=str(build_dir / "tests" / "checkasm" / "checkasm"),
    )
    aid = task.save_artifact("BUILD", build_artifact, sub_id=build_artifact.run_id)
    task.artifacts.build_run_ids.append(build_artifact.run_id)
#TODO：会出现因为实际没有生成有效内容而checkasm能够通过，导致误判为成功的情况。是否应该添加一个检查？
    if make_result.returncode == 0:
        print(f"\n构建成功 ✓")
        record_trajectory_action("build_success", "Build succeeded")
        task.current_state = TaskState.KB_UPDATE
    else:
        print(f"\n构建失败 (rc={make_result.returncode})")
        # Save build log with extracted errors
        error_extract = extract_build_errors(make_result.stdout + make_result.stderr)
        build_log_path = task.run_dir / "build_log.txt"
        # Append to existing log (may have configure output from earlier runs)
        existing_log = ""
        if build_log_path.exists():
            existing_log = build_log_path.read_text(encoding="utf-8", errors="replace")
        write_text(build_log_path,
                   existing_log + f"\n=== make (rc={make_result.returncode}) ===\n{error_extract}\n")
        record_trajectory_action("build_fail", "Build failed")
        task.current_state = TaskState.DEBUG

    return task


def handle_debug(task: TaskContext, kb: KnowledgeBase | None = None) -> TaskContext:
    """DEBUG handler: delegates to structured debug module."""
    from .debug import run_debug_handler
    return run_debug_handler(task, kb=kb)


def handle_test(task: TaskContext) -> TaskContext:
    """TEST handler: placeholder for board/qemu testing."""
    from ..tool.board import build_board_commands, local_checkasm_path, run_with_sshpass

    if not task.cfg.board.enabled:
        print("\n未启用 board 配置，跳过板端测试。")
        task.current_state = TaskState.KB_UPDATE
        return task

    cmds = build_board_commands(task.cfg, task.ffmpeg_root)
    local_bin = local_checkasm_path(task.ffmpeg_root, str(task.cfg.ffmpeg.build_dir))

    if not local_bin.exists():
        print(f"\n本地 checkasm 不存在：{local_bin}，跳过板端测试。")
        task.current_state = TaskState.KB_UPDATE
        return task

    scp_ok = task.cfg.human.scp_ok
    if scp_ok is None:
        print("\n将把 checkasm scp 到测试板：")
        print("- " + fmt_argv(cmds.scp_argv))
        scp_ok = prompt_yes_no("是否现在执行 scp？", default=False)
        task.cfg.human.scp_ok = scp_ok

    if scp_ok:
        password = task.cfg.human.scp_password
        if not password:
            password = prompt_secret("请输入测试板 SSH 密码： ")
            task.cfg.human.scp_password = password
        res_scp = run_with_sshpass(cmds.scp_argv, password)
        write_text(task.run_dir / "scp_stdout.txt", res_scp.stdout)

    run_ok = task.cfg.human.run_onboard_ok
    if run_ok is None:
        run_ok = prompt_yes_no("是否在测试板上运行 checkasm？", default=False)
        task.cfg.human.run_onboard_ok = run_ok

    if run_ok:
        password = task.cfg.human.scp_password
        if not password:
            password = prompt_secret("请输入测试板 SSH 密码： ")
            task.cfg.human.scp_password = password
        res_run = run_with_sshpass(cmds.ssh_run_argv, password)
        write_text(task.run_dir / "board_stdout.txt", res_run.stdout)

    task.save_artifact("TEST", {"status": "completed", "scp_ok": scp_ok, "run_ok": run_ok})
    task.current_state = TaskState.KB_UPDATE
    return task


def handle_kb_update(task: TaskContext, kb: KnowledgeBase | None = None) -> TaskContext:
    """KB_UPDATE handler: extract patterns from successful migration."""
    if kb is None:
        task.save_artifact("KB_UPDATE", KBUpdateArtifact())
        task.current_state = TaskState.DONE
        return task

    # Load analysis and retrieval for richer extraction
    try:
        analysis = task.load_artifact("ANALYZE")
    except Exception:
        analysis = {}
    try:
        retrieval = task.load_artifact("RETRIEVE")
    except Exception:
        retrieval = {}

    analysis_json = analysis.get("analysis_json", {})
    selected_files = retrieval.get("selected_files", [])
    symbol = task.target.symbol

    # Build architecture field from file presence
    arch_info: dict[str, list[str]] = {}
    for f in selected_files:
        fl = f.lower()
        if "/x86/" in fl or "_sse" in fl or "_avx" in fl:
            arch_info.setdefault("x86", []).append(f)
        elif "/aarch64/" in fl or "/arm/" in fl or "_neon" in fl:
            arch_info.setdefault("neon", []).append(f)
        elif "/riscv/" in fl or "_rvv" in fl:
            arch_info.setdefault("rvv", []).append(f)

    # Build source field with c_paths
    c_paths = [f for f in selected_files if f.endswith((".c", ".h"))]

    # Extract semantic tags from analysis
    algo_class = "unknown"
    tags: list[str] = []
    if isinstance(analysis_json.get("pattern"), list):
        tags = [str(t) for t in analysis_json["pattern"]]
        algo_class = tags[0] if tags else "unknown"
    elif analysis_json.get("pattern"):
        algo_class = str(analysis_json["pattern"])
        tags = [algo_class]

    # Create a pattern from this successful migration
    new_pattern = Pattern(
        pattern_id=f"{symbol}_{task.task_id}",
        source={"symbol": symbol, "c_paths": c_paths},
        semantic_ir={
            "algorithm_class": algo_class,
            "tags": tags,
            "loop": analysis_json.get("loop_structure", ""),
            "memory_pattern": analysis_json.get("memory_access", ""),
        },
        simd_strategy={
            "vectorize": analysis_json.get("vectorizable", True),
            "reduction": analysis_json.get("reduction", False),
            "tail_handling": "mask" if analysis_json.get("tail_required") else "none",
            "unroll": analysis_json.get("unroll_factor", 1),
        },
        architecture=arch_info,
        metadata={"weight": 0.5, "success_count": 1, "fail_count": 0},
        notes=f"Auto-extracted from migration of {symbol}",
    )
    kb.add_pattern(new_pattern)

    # Update weight for any patterns that were used during PLAN/PATCH
    # (build succeeded if we reached KB_UPDATE)
    for pid in task.artifacts.patch_ids:
        # The pattern_id format is "{symbol}_{task_id}", try to find matching
        existing = kb.search_patterns(symbol=symbol, max_results=5)
        for p in existing:
            if p.pattern_id != new_pattern.pattern_id:
                kb.update_weight(p.pattern_id, success=True)

    # Record any debug errors as error patterns
    new_errors: list[dict] = []
    for err_text in task.all_build_errors:
        from .debug import classify_error
        err_class = classify_error(err_text)
        record = ErrorRecord(
            error_class=err_class.value,
            pattern=err_text[:200],
            fix_strategy="auto-fixed during migration",
            example=err_text[:500],
        )
        kb.add_error(record)
        new_errors.append({"error_class": err_class.value, "pattern": err_text[:200]})

    artifact = KBUpdateArtifact(
        new_patterns=[{"pattern_id": new_pattern.pattern_id, "symbol": symbol}],
        new_errors=new_errors,
    )
    task.save_artifact("KB_UPDATE", artifact)
    record_trajectory_action("kb_update", f"KB updated: 1 pattern, {len(new_errors)} errors")

    task.current_state = TaskState.DONE
    return task


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_chat(cfg: AppConfig) -> int:
    """Interactive chat mode with state-machine driven migration."""
    print("rvv-agent chat：自由对话 + 迁移任务触发模式")
    print("- 普通问题：直接提问即可（会保留上下文）。")
    print("- 触发迁移：FFmpeg/libav 语境 + 迁移/rvv/simd/checkasm 等关键词。")
    print("- 退出：按 Ctrl+C，或输入 /exit。\n")

    _print_llm_probe(cfg)
    print("")

    # Load knowledge base
    kb = KnowledgeBase(Path("knowledge_base.json"))
    kb.load()

    # Chat history for normal conversation
    history: list[LlmMessage] = [LlmMessage(role="system", content=system_prompt())]

    while True:
        try:
            user_text = prompt_text("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye")
            return 0

        if not user_text:
            continue
        if user_text.lower() in {"/exit", "/quit", ":q"}:
            return 0

        # Parse intent
        from .intent import parse_intent
        intent = parse_intent(cfg, user_text)

        if intent.action != "migrate":
            # Normal chat
            history.append(LlmMessage(role="user", content=user_text))
            if len(history) > 1 + 16:
                history = [history[0], *history[-16:]]
            try:
                reply = chat_completion(cfg.llm, history, max_tokens=800, stage="chat").strip()
                print(reply + "\n")
                history.append(LlmMessage(role="assistant", content=reply))
            except Exception as e:
                print_llm_error(e, "chat")
                print(f"错误：{e}\n")
            continue

        # ===== Migrate workflow (state machine) =====
        symbol = intent.symbol
        if not symbol:
            print("已识别为迁移任务，但没从输入中抽取到算子/函数名。")
            symbol = _prompt_symbol()
            if not symbol:
                print("已取消或未提供有效 symbol，本轮结束。\n")
                continue

        # Build MigrationTarget
        target = intent.target or MigrationTarget(
            module=symbol.split(".")[0] if "." in symbol else symbol,
            symbol=symbol,
        )

        # Create TaskContext
        task_id = now_id()
        run_dir = Path("runs") / f"{task_id}_{slug(symbol)}"
        ensure_dir(run_dir)

        task = TaskContext(
            task_id=task_id,
            target=target,
            current_state=TaskState.INTENT,
            run_dir=run_dir,
            cfg=cfg,
            ffmpeg_root=cfg.ffmpeg.root.expanduser().resolve(),
        )

        # Save user input
        write_text(run_dir / "user_input.txt", user_text + "\n")
        reset_trajectory()

        # Register state handlers
        handlers = {
            TaskState.INTENT: handle_intent,
            TaskState.RETRIEVE: handle_retrieve,
            TaskState.ANALYZE: handle_analyze,
            TaskState.PLAN: lambda t: handle_plan(t, kb),
            TaskState.PATCH: lambda t: handle_patch(t, kb),
            TaskState.BUILD: handle_build,
            TaskState.DEBUG: lambda t: handle_debug(t, kb),
            TaskState.TEST: handle_test,
            TaskState.KB_UPDATE: lambda t: handle_kb_update(t, kb),
        }

        # Run state machine
        sm = StateMachine(task, handlers)
        try:
            task = sm.run()
        except Exception as e:
            print_red(f"\n迁移过程出错: {e}")
            import traceback
            traceback.print_exc()

        # Generate report
        try:
            from .report import write_chat_report
            rpt = write_chat_report(task)
            print(f"报告已生成: {rpt}")
        except Exception as e:
            print_yellow(f"报告生成失败: {e}")

        # Save trajectory
        traj = get_trajectory_dict(model=cfg.llm.model, endpoint=cfg.llm.base_url)
        write_json(run_dir / "trajectory.json", traj)
        tot = traj.get("totals", {})
        print(
            f"\n[trajectory] calls={tot.get('num_calls', 0)}"
            f"  in={tot.get('input_tokens', 0)}"
            f"  out={tot.get('output_tokens', 0)}"
            f"  cost=${tot.get('cost_usd', 0.0):.6f}"
        )

        # Save KB
        kb.save()

        print(f"\n本轮完成：run_dir = {run_dir}\n")
