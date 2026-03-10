from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .generate import analyze_with_llm
from ..tool.board import build_board_commands, local_checkasm_path, run_with_sshpass
from ..core.config import AppConfig
from .search import build_context_from_files
from ..tool.exec import (
    ExecResult,
    configure_argv,
    make_checkasm_argv,
    run_configure,
    run_make_checkasm,
)
from .generate import (
    fix_generation_with_llm,
    generate_with_llm,
)
from .inject import inject_generate_plan
from .intent import parse_intent
from ..tool.interactive import prompt_secret, prompt_text, prompt_yes_no
from ..core.llm import LlmMessage, chat_completion, probe_llm, reset_trajectory, get_trajectory_dict, record_trajectory_action
from .generate import fixed_plan, llm_plan
from ..core.prompts import files_refine_prompt, plan_refine_prompt, system_prompt
from .report import write_report
from .search import select_references
from ..core.util import ensure_dir, extract_build_errors, fmt_argv, now_id, slug, write_text, print_llm_error, print_red, print_yellow
from ..core.context import DynamicContext


@dataclass
class SessionContext:
    """Persistent, human-editable context carried across all turns in a session.

    Three categories of context are kept alive throughout the whole session:
      1. plan        – migration steps (LLM-generated, human-refineable)
      2. ref_files   – selected reference file list (LLM-generated, human-refineable)
      3. credentials – board SSH password (entered once, reused silently)

    Plus the one-time human decisions that are remembered session-wide:
      apply_ok, build_ok, scp_ok, run_on_board_ok
    """
    # ── sticky user decisions (asked once per session) ──
    apply_ok: bool | None = None
    build_ok: bool | None = None
    scp_ok: bool | None = None
    run_on_board_ok: bool | None = None

    # ── credential (entered once, never re-prompted) ──
    scp_password: str | None = None

    # ── persistent, refineable context ──
    # Keyed by symbol so multiple migrations in one session stay independent.
    plans: dict[str, list[str]] = field(default_factory=dict)        # symbol → steps
    ref_files: dict[str, list[str]] = field(default_factory=dict)    # symbol → file list
    refine_history: list[dict] = field(default_factory=list)          # [{stage, feedback}]

    # ── dynamic context per symbol (plan + analysis + build errors, never compressed) ──
    dynamic_contexts: dict[str, DynamicContext] = field(default_factory=dict)

    def get_dctx(self, symbol: str) -> DynamicContext:
        """Get or create the DynamicContext for a given symbol."""
        if symbol not in self.dynamic_contexts:
            self.dynamic_contexts[symbol] = DynamicContext(symbol=symbol)
        return self.dynamic_contexts[symbol]


# Keep old name as alias so pipeline.py still works without changes
SessionState = SessionContext


def _print_llm_probe(cfg: AppConfig) -> None:
    st = probe_llm(cfg.llm)
    print("LLM status:")
    print(f"- endpoint_url: {st.get('endpoint_url')}")
    print(f"- model: {st.get('model')}")
    print(f"- api_key_env: {st.get('api_key_env')}")
    print(f"- api_key_present: {st.get('api_key_present')}")
    print(f"- probe_ok: {st.get('probe_ok')}")
    print(f"- endpoint_url_normalized: {st.get('endpoint_url_normalized')}")
    if st.get("probe_ok"):
        print(f"- probe_reply: {st.get('probe_reply')}")
    else:
        print(f"- probe_error: {st.get('probe_error')}")


def _prompt_symbol() -> str:
    follow = prompt_text(
        "请直接输入要迁移的算子/函数名（或输入 /cancel 取消本次迁移）： "
    ).strip()
    if follow.lower() in {"/cancel", "/c"}:
        return ""

    # Accept either a bare identifier or a sentence containing one.
    m = re.search(r"([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)", follow)
    return m.group(1) if m else ""



def _refine_plan(cfg: AppConfig, symbol: str, steps: list[str], history: list[dict] | None = None) -> list[str]:
    """Enter an interactive refine loop for the plan. Returns the (possibly updated) steps."""
    while True:
        feedback = prompt_text(
            "请描述修改意见（直接回车接受，输入 /skip 跳过本次迁移）：\n> "
        ).strip()
        if not feedback:
            return steps
        if feedback.lower() in {"/skip", "/cancel"}:
            return []  # empty → caller should cancel
        from ..core.llm import LlmMessage, chat_completion
        from ..core.prompts import plan_refine_prompt, system_prompt
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
                import json
                data = json.loads(raw[start : end + 1])
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


def _refine_files(cfg: AppConfig, symbol: str, files: list[str], history: list[dict] | None = None) -> list[str]:
    """Enter an interactive refine loop for the reference file list. Returns updated list."""
    while True:
        feedback = prompt_text(
            "请描述修改意见（直接回车接受，输入 /skip 跳过本次迁移）：\n> "
        ).strip()
        if not feedback:
            return files
        if feedback.lower() in {"/skip", "/cancel"}:
            return []
        from ..core.llm import LlmMessage, chat_completion
        from ..core.prompts import files_refine_prompt, system_prompt
        import json
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
                data = json.loads(raw[start : end + 1])
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


def run_chat(cfg: AppConfig) -> int:
    print("rvv-agent chat：自由对话 + 迁移任务触发模式")
    print("- 普通问题：直接提问即可（会保留上下文）。")
    print(
        "- 触发迁移：FFmpeg/libav 语境 + 迁移/rvv/simd/checkasm/编译/生成 等关键词。"
    )
    print("- 退出：按 Ctrl+C，或输入 /exit。\n")

    _print_llm_probe(cfg)
    print("")

    state = SessionState()

    # Conversation history for normal chat (keep small)
    history: list[LlmMessage] = [LlmMessage(role="system", content=system_prompt())]

    while True:
        try:
            user_text = prompt_text("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye")
            return 0

        if not user_text:
            continue

        # Prefer Ctrl+C to exit. Use /exit for an explicit command.
        if user_text.lower() in {"/exit", "/quit", ":q"}:
            return 0

        intent = parse_intent(cfg, user_text)

        if intent.action != "migrate":
            # Normal chat mode
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

        # ===== migrate workflow (human-in-the-loop) =====
        symbol = intent.symbol
        if not symbol:
            print("已识别为迁移任务，但没从输入中抽取到算子/函数名。")
            symbol = _prompt_symbol()
            if not symbol:
                print("已取消或未提供有效 symbol，本轮结束。\n")
                continue

        # Create run directory and record user input first (must be before any usage of run_dir)
        run_dir = Path("runs") / f"{now_id()}_{slug(symbol)}"
        ensure_dir(run_dir)
        _user_input_lines = [user_text]
        write_text(run_dir / "user_input.txt", user_text + "\n")
        write_text(run_dir / "intent_raw.txt", intent.raw + "\n")

        # Reset LLM trajectory for this pipeline run
        reset_trajectory()

        # Get/create the DynamicContext for this symbol (carries plan, analysis, build errors)
        dctx = state.get_dctx(symbol)

        # Use cached plan for this symbol if already refined in this session
        if symbol in state.plans:
            plan_steps = state.plans[symbol]
            print("\n（使用本次会话中已确认的计划）")
        else:
            print("\n📖正在生成迁移计划…")
            _p = llm_plan(cfg, symbol)
            plan_steps = _p.steps

        # Display concise fixed 8-step summary; LLM-generated detail stays internal
        _DISPLAY_STEPS = [
            f"意图解析：迁移 {symbol}",
            "定位 C 实现",
            "定位 x86 / ARM 参考实现",
            "语义抽象（结构化任务描述 JSON）",
            "（MVP）调用 LLM 生成 RVV asm + init + Makefile patch（先落到 runs/）",
            "（可选）把补丁应用到 workspace",
            "（可选）交叉 configure + build checkasm",
            "生成 run 报告（轨迹、输入输出、命令、摘要）",
        ]
        print("\nPlan：")
        for i, s in enumerate(_DISPLAY_STEPS, start=1):
            print(f"  {i}. {s}")

        if not prompt_yes_no("\n确认按该 plan 继续？", default=True):
            plan_steps = _refine_plan(cfg, symbol, plan_steps, history=dctx.refine_history)
            if not plan_steps:
                print("已取消，本轮结束👋。\n")
                continue
            _user_input_lines.append("[plan-refined]")
        state.plans[symbol] = plan_steps
        dctx.update_plan(plan_steps)  # 同步到 DynamicContext（不可压缩）
        record_trajectory_action(
            "plan",
            f"Migration plan confirmed for {symbol}",
            detail="\n".join(plan_steps),
            event_type="human_output",
        )
        # Update user_input log with any refine feedback
        write_text(run_dir / 'user_input.txt', '\n'.join(_user_input_lines) + '\n')
        from .generate import Plan as _Plan
        plan = _Plan(steps=plan_steps)

        ffmpeg_root = cfg.ffmpeg.root.expanduser().resolve()
        if not ffmpeg_root.exists():
            print(f"ffmpeg_root 不存在：{ffmpeg_root}")
            continue

        retrieval = select_references(cfg, ffmpeg_root, intent)
        write_text(run_dir / "retrieval_raw.txt", retrieval.raw_text + "\n")
        selected = retrieval.selected

        def _list(key: str) -> list[str]:
            v = selected.get(key, [])
            return [str(x) for x in v] if isinstance(v, list) else []

        selected_files: list[str] = []
        for k in ("c", "x86", "arm", "riscv", "headers", "makefiles", "checkasm"):
            selected_files.extend(_list(k))
        selected_files = list(dict.fromkeys(selected_files))

        # Merge existing RVV files into selected_files (deduplicated)
        for _r in retrieval.existing_rvv:
            if _r not in selected_files:
                selected_files.append(_r)

        print("\n检索/选择出的参考文件：")
        for p in selected_files:
            _tag = "[existing-rvv] " if p in retrieval.existing_rvv else ""
            print(f"  {_tag}{p}")

        # Use cached file list for this symbol if already refined
        if symbol in state.ref_files:
            selected_files = state.ref_files[symbol]
            print("\n（使用本次会话中已确认的参考文件列表）")
            print("\n参考文件：")
            for _f in selected_files:
                print(f"  - {_f}")

        if not prompt_yes_no("\n确认进入分析/生成阶段？", default=True):
            selected_files = _refine_files(cfg, symbol, selected_files, history=dctx.refine_history)
            if not selected_files:
                print("已取消，本轮结束。\n")
                continue
        state.ref_files[symbol] = selected_files
        record_trajectory_action(
            "select_refs",
            f"Reference files confirmed ({len(selected_files)} files)",
            detail="\n".join(selected_files),
            event_type="human_output",
        )
        # Persist final selected files back to retrieval_raw
        import json as _json
        write_text(run_dir / 'retrieval_raw.txt',
                   retrieval.raw_text + '\n\n# FINAL SELECTED:\n' +
                   _json.dumps(selected_files, ensure_ascii=False, indent=2) + '\n')

        ctx = build_context_from_files(ffmpeg_root, symbol=symbol, files=selected_files)
        write_text(run_dir / "context.txt", ctx)
        dctx.code_context = ctx  # 同步完整代码上下文到 DynamicContext（不截断）

        print("\n⚙ 正在分析算子实现…")
        # 传入已有分析（refine 时修正）和历次构建错误（不可省略）
        analysis = analyze_with_llm(
            cfg,
            retrieval.discovery,
            context_override=ctx,
            prior_analysis=dctx.analysis if dctx.analysis else None,
            build_errors=dctx.build_errors_for_llm() or None,
        )
        # 更新 DynamicContext 中的 analysis（不可压缩）
        dctx.update_analysis(analysis.analysis, analysis.raw_text)
        record_trajectory_action(
            "analyze",
            f"Operator analysis complete (llm_used={analysis.llm_used})",
            detail=analysis.raw_text[:2000],
            event_type="human_output",
        )
        # 落盘到 run_dir/analysis.json
        from ..core.util import write_json as _wj_a
        _wj_a(run_dir / "analysis.json", analysis.analysis)

        # Build existing_map from selected_files (non-.S files already on disk).
        # selected_files already contains retrieval.existing_rvv, so no extra
        # rescan of libavcodec/riscv/ is needed.
        existing_map: dict[str, str] = {}
        for _rel in selected_files:
            _full = ffmpeg_root / _rel
            if _full.exists() and _full.is_file() and not _rel.endswith(".S"):
                try:
                    existing_map[_rel] = _full.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    pass
        if existing_map:
            print("\n以下现有文件将作为 LLM 增量生成基础（来自 reference 集合）：")
            for _ep in existing_map:
                print(f"  [existing] {_ep}")

        print("\n⚙ 正在调用 LLM 生成 RVV 代码…（可能需要 20–60 秒）")
        gen = generate_with_llm(cfg, symbol, analysis.analysis,
                               existing_files_map=existing_map or None)
        if gen.error:
            print_llm_error(gen.error, "generate")

        # ── Step 1: Apply generated files to FFmpeg workspace ─────────────────
        if state.apply_ok is None:
            state.apply_ok = prompt_yes_no(
                "\n是否把生成文件写入 FFmpeg workspace（workplace/FFmpeg）？",
            )

        # Inject generated files to FFmpeg workspace (append-only, never overwrites)
        _inject_result = inject_generate_plan(
            run_dir,
            ffmpeg_root,
            gen.generate_plan,
            apply=bool(state.apply_ok),
            attempt=0,
            cfg=cfg,
        )
        materialized = _inject_result.applied_paths
        record_trajectory_action(
            "generate",
            f"Code generated (llm_used={gen.llm_used}), apply={state.apply_ok}",
            detail=f"files: {[i.get('target_path','') for i in gen.generate_plan.get('generated', [])]}",
        )
        if state.apply_ok:
            print("\n已将生成文件注入 FFmpeg workspace（append 模式，不覆盖已有代码）：")
            for _mp in materialized:
                _mp_r = Path(_mp)
                if _mp_r.is_relative_to(ffmpeg_root):
                    print(f"  → {_mp_r}")
        else:
            print("\n已将生成计划保存到 runs/（未写入 FFmpeg）。")

        exec_result = ExecResult()
        import os
        jobs = max(1, os.cpu_count() or 1)
        build_dir = ffmpeg_root / cfg.ffmpeg.build_dir

        # ── Step 2: Cross-compile (configure + make checkasm) ─────────────────
        if state.build_ok is None:
            print("\n交叉编译计划：")
            print(f"  build 目录 : {build_dir}")
            print(f"  configure  : {fmt_argv(configure_argv(cfg, ffmpeg_root))}")
            print(f"  make       : {fmt_argv(make_checkasm_argv(jobs=jobs))}")
            state.build_ok = prompt_yes_no(
                "\n是否现在执行 configure + 构建 checkasm？",
            )

        MAX_BUILD_RETRIES = 3
        build_attempt = 0
        if state.build_ok:
            ensure_dir(build_dir)
            print(f"\n已创建/确认 build 目录：{build_dir}")

            # ── configure + make retry loop (shared counter) ──────────────────
            while build_attempt <= MAX_BUILD_RETRIES:
                # ─ configure phase ─
                print(f"\n正在运行 configure（第 {build_attempt+1} 次尝试，输出实时显示）…")
                exec_result.configure = run_configure(cfg, ffmpeg_root, build_dir)
                # 追加本次 configure 输出到统一日志
                with open(run_dir / "build_log.txt", "a", encoding="utf-8") as _bl:
                    _bl.write(f"\n{'='*60}\n")
                    _bl.write(f"configure attempt {build_attempt+1}\n")
                    _bl.write(f"{'='*60}\n")
                    _bl.write(exec_result.configure.stdout)

                if exec_result.configure.returncode != 0:
                    cfg_err = extract_build_errors(exec_result.configure.stdout)
                    print(f"\nconfigure 失败（returncode={exec_result.configure.returncode}）")
                    build_attempt += 1
                    if build_attempt > MAX_BUILD_RETRIES:
                        print(f"\nconfigure 已连续失败 {MAX_BUILD_RETRIES} 次，请人工处理。")
                        print(f"错误信息片段（智能提取）：\n{cfg_err[-800:]}\n")
                        break
                    if not prompt_yes_no(
                        f"\n是否让 LLM 分析 configure 错误并迭代修复（第 {build_attempt}/{MAX_BUILD_RETRIES} 次）？",
                        default=True,
                    ):
                        print("已取消迭代，本次构建结束。")
                        break
                    print(f"\n正在让 LLM 修复（configure 错误）…")
                    # 追加构建错误到 DynamicContext（永久保留）
                    dctx.append_build_error("configure", build_attempt, cfg_err)
                    record_trajectory_action(
                        "build_error_configure",
                        f"Configure failed (attempt {build_attempt})",
                        detail=cfg_err[-1000:],
                    )
                    fix_result = fix_generation_with_llm(
                        cfg, symbol, cfg_err, gen.generate_plan,
                        analysis=dctx.analysis or None,
                        all_prior_errors=dctx.build_errors_for_llm() or None,
                    )
                    if fix_result.error:
                        print_llm_error(fix_result.error, f"fix/configure#{build_attempt}")
                        print(f"LLM 修复请求失败：{fix_result.error}")
                        break
                    gen = fix_result
                    write_text(run_dir / f"fix_attempt{build_attempt}_configure_raw.txt", gen.raw_text)
                    if state.apply_ok:
                        inject_generate_plan(run_dir, ffmpeg_root, gen.generate_plan,
                                             apply=True, attempt=build_attempt, cfg=cfg)
                        print(f"已将修复后的文件重新注入 FFmpeg workspace（configure 修复 #{build_attempt}，append 模式）")
                    record_trajectory_action(
                        "fix_inject",
                        f"Configure fix #{build_attempt} injected",
                    )
                    continue  # retry configure with fixed files

                # ─ make phase ─
                print("\nconfigure 完成，开始构建 checkasm…")
                exec_result.make_checkasm = run_make_checkasm(cfg, build_dir, jobs)
                # 追加本次 make 输出到统一日志
                with open(run_dir / "build_log.txt", "a", encoding="utf-8") as _bl:
                    _bl.write(f"\n{'='*60}\n")
                    _bl.write(f"make checkasm attempt {build_attempt+1}\n")
                    _bl.write(f"{'='*60}\n")
                    _bl.write(exec_result.make_checkasm.stdout)
                    _bl.write(exec_result.make_checkasm.stderr)
                if exec_result.make_checkasm.returncode == 0:
                    print(f"\n构建成功（第 {build_attempt+1} 次尝试）✓")
                    record_trajectory_action(
                        "build_success",
                        f"Build succeeded (attempt {build_attempt+1})",
                    )
                    break

                # make failed
                make_err = extract_build_errors(
                    exec_result.make_checkasm.stdout + exec_result.make_checkasm.stderr
                )
                build_attempt += 1
                if build_attempt > MAX_BUILD_RETRIES:
                    print(f"\n构建连续失败 {MAX_BUILD_RETRIES} 次，请人工处理。")
                    print(f"错误信息片段（智能提取）：\n{make_err[-800:]}\n")
                    break
                if not prompt_yes_no(
                    f"\n是否让 LLM 分析 make 错误并迭代修复（第 {build_attempt}/{MAX_BUILD_RETRIES} 次）？",
                    default=True,
                ):
                    print("已取消迭代，本次构建结束。")
                    break
                print(f"\n正在让 LLM 修复（make 错误）…")
                # 追加构建错误到 DynamicContext（永久保留）
                dctx.append_build_error("make", build_attempt, make_err)
                record_trajectory_action(
                    "build_error_make",
                    f"Make checkasm failed (attempt {build_attempt})",
                    detail=make_err[-1000:],
                )
                fix_result = fix_generation_with_llm(
                    cfg, symbol, make_err, gen.generate_plan,
                    analysis=dctx.analysis or None,
                    all_prior_errors=dctx.build_errors_for_llm() or None,
                )
                if fix_result.error:
                    print_llm_error(fix_result.error, f"fix/make#{build_attempt}")
                    print(f"LLM 修复请求失败：{fix_result.error}")
                    break
                gen = fix_result
                write_text(run_dir / f"fix_attempt{build_attempt}_raw.txt", gen.raw_text)
                if state.apply_ok:
                    inject_generate_plan(run_dir, ffmpeg_root, gen.generate_plan,
                                         apply=True, attempt=build_attempt, cfg=cfg)
                    print(f"已将修复后的文件重新注入 FFmpeg workspace（make 修复 #{build_attempt}，append 模式）")
                record_trajectory_action(
                    "fix_inject",
                    f"Make fix #{build_attempt} injected",
                )
                # continue loop: re-run configure then make with fixed files

        # Board steps
        if cfg.board.enabled:
            cmds = build_board_commands(cfg, ffmpeg_root)
            local_bin = local_checkasm_path(ffmpeg_root, str(cfg.ffmpeg.build_dir))

            if not local_bin.exists():
                print(f"\n本地 checkasm 不存在：{local_bin}")
                print("请先在本机执行构建（或在 chat 里开启 build）。将跳过 scp/板端运行。")
                state.scp_ok = False
                state.run_on_board_ok = False
            else:
                if state.scp_ok is None:
                    print("\n将把 checkasm scp 到测试板：")
                    print("- " + fmt_argv(cmds.scp_argv))
                    state.scp_ok = prompt_yes_no("是否现在执行 scp？", default=False)

                if state.scp_ok:
                    if state.scp_password is None:
                        print(
                            "注意：使用 sshpass 会把密码暴露在进程参数里；长期建议改用 SSH key。"
                        )
                        state.scp_password = prompt_secret(
                            "请输入测试板 SSH 密码（本次对话仅输入一次）： "
                        )
                    res_scp = run_with_sshpass(cmds.scp_argv, state.scp_password)
                    write_text(run_dir / "scp_stdout.txt", res_scp.stdout)
                    write_text(run_dir / "scp_stderr.txt", res_scp.stderr)

                if state.run_on_board_ok is None:
                    print("\n测试板上运行 checkasm 命令：")
                    print("- " + fmt_argv(cmds.ssh_run_argv))
                    state.run_on_board_ok = prompt_yes_no(
                        "是否现在在测试板上运行 checkasm？",
                        default=False,
                    )

                if state.run_on_board_ok:
                    if state.scp_password is None:
                        print(
                            "注意：使用 sshpass 会把密码暴露在进程参数里；长期建议改用 SSH key。"
                        )
                        state.scp_password = prompt_secret(
                            "请输入测试板 SSH 密码（本次对话仅输入一次）： "
                        )
                    res_run = run_with_sshpass(cmds.ssh_run_argv, state.scp_password)
                    write_text(run_dir / "board_stdout.txt", res_run.stdout)
                    write_text(run_dir / "board_stderr.txt", res_run.stderr)
        else:
            if state.scp_ok is None:
                print("\n未启用 board 配置（rvv_agent.toml [board]）。将跳过 scp/板端运行。")
                state.scp_ok = False
                state.run_on_board_ok = False

        # Save DynamicContext for this run (includes plan, analysis, build errors)
        from ..core.util import write_json as _wj_dctx
        _wj_dctx(run_dir / "dynamic_context.json", {
            "symbol": dctx.symbol,
            "plan_steps": dctx.plan_steps,
            "analysis": dctx.analysis,
            "build_errors": dctx.build_errors,
            "refine_history": dctx.refine_history,
        })
        print(f"\n[dynamic-context] {dctx.to_summary()}")
        print(f"  → {run_dir}/dynamic_context.json")

        # Save LLM trajectory for this run
        _traj = get_trajectory_dict(
            model=cfg.llm.model,
            endpoint=cfg.llm.base_url,
        )
        from ..core.util import write_json as _wj
        _wj(run_dir / "trajectory.json", _traj)
        _tot = _traj.get("totals", {})
        print(
            f"\n[trajectory] calls={_tot.get('num_calls',0)}"
            f"  in={_tot.get('input_tokens',0)}"
            f"  out={_tot.get('output_tokens',0)}"
            f"  cost=${_tot.get('cost_usd',0.0):.6f}"
            f"  → {run_dir}/trajectory.json"
        )

        report_path = write_report(
            run_dir,
            plan=plan,
            discovery=retrieval.discovery,
            analysis=analysis,
            generation_raw=gen.raw_text,
            materialized=materialized,
            exec_result=exec_result,
            ref_files=selected_files,
            refine_history=dctx.refine_history if dctx.refine_history else None,
            interaction={
                "intent_action": intent.action,
                "intent_llm_used": intent.llm_used,
                "intent_error": intent.error,
                "retrieval_llm_used": retrieval.llm_used,
                "retrieval_error": retrieval.error,
                "apply_ok": state.apply_ok,
                "build_ok": state.build_ok,
                "build_attempts": build_attempt + 1,
                "scp_ok": state.scp_ok,
                "run_on_board_ok": state.run_on_board_ok,
                "board_enabled": cfg.board.enabled,
            },
        )

        print(f"\n本轮完成：report = {report_path}\n")
