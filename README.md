# rvv-agent

面向 FFmpeg 的 RVV (RISC-V Vector) 迁移命令行工具。当前版本基于状态机驱动，支持 `chat` 交互式流程和 `migrate` 非交互式流水线，包含 LLM 检索/分析/补丁生成、构建校验、失败回滚与知识库更新。

## 功能概览

- 意图解析：在 `chat` 模式下支持普通问答与迁移任务自动识别。
- 分层检索：先按 `symbol` 检索，必要时按 `module` 补充检索并去重。
- 函数发现：`FUNC_DISCOVER` 阶段识别模块中可迁移函数，并形成 `function_order`。
- 结构化补丁：`PATCH` 阶段采用 `locate -> design -> generate -> apply` 四步。
- 增量注入：`.S` 文件优先追加，避免覆盖已有 RVV 代码；文本文件按锚点插入或末尾注入。
- 调试闭环：`BUILD` 失败进入 `DEBUG`，按错误类型给出回滚目标并重试。
- 全量落盘：每次运行写入 `runs/<task_id>_<symbol>/`，含状态文件、轨迹、报告、构建日志。
- 经验记忆：成功迁移 pattern 与常见错误写入 `knowledge_base.json`，后续可被检索复用。

## 当前目录结构

```text
bin/rvv-agent
rvv_agent.toml
knowledge_base.json
runs/
workplace/FFmpeg/

rvv_agent/
  cli.py
  pipeline.py
  core/
    config.py
    llm.py
    statemachine.py
    task.py
    util.py
    prompts.py
    prompts_patch.py
  agent/
    chat.py
    intent.py
    search.py
    analyze.py
    plan.py
    patch.py
    debug.py
    report.py
    inject.py
  tool/
    exec.py
    board.py
    interactive.py
  memory/
    knowledge_base.py
    pattern_lib.py
```

说明：当前实现中不再有 `agent/generate.py`，生成逻辑已并入 `agent/patch.py` 的四步流程。

## 快速开始

### 1. 配置

编辑 `rvv_agent.toml`：

```toml
[llm]
base_url    = "https://your-endpoint/v1"   # 若未以 /chat/completions 结尾，会自动补全
model       = "gpt-4o-mini"
api_key_env = "LLM_API_KEY"
temperature = 0.2
# 可选：用于 trajectory 成本估算
# cost_per_1m_input_tokens = 0
# cost_per_1m_output_tokens = 0

[toolchain]
cross_prefix = "riscv64-unknown-linux-gnu-"
arch = "riscv64"
target_os = "linux"
cpu = "rv64gcv"
extra_cflags = "-march=rv64gcv -mabi=lp64d -O3"
extra_ldflags = "-static"
extra_path = "/path/to/riscv-toolchain/bin"  # 执行 configure/make 时会 prepend 到 PATH

[ffmpeg]
root = "workplace/FFmpeg"
build_dir = "build"
# configure_path = "workplace/FFmpeg/configure"
# configure_extra_args = ["--disable-everything"]

[board]
enabled = false
user = ""
host = ""
port = 22
remote_dir = "workplace"

[human]
# null 表示运行时询问；true/false 表示跳过询问直接执行
# apply_ok = true
# exec_ok = false
# scp_ok = false
# run_onboard_ok = false
scp_password = ""
```

设置 API key：

```bash
export LLM_API_KEY='sk-...'
```

### 2. 交互模式（推荐）

```bash
./bin/rvv-agent chat
```

`chat` 模式要点：

- 普通问答：直接对话，保留最近上下文。
- 迁移触发：输入包含 FFmpeg/libav 语境 + 迁移关键词时进入状态机流程。
- 迁移状态流：`INTENT -> RETRIEVE -> FUNC_DISCOVER -> PLAN -> ANALYZE -> PATCH -> BUILD -> DEBUG(retry) -> KB_UPDATE -> DONE`。
- 每轮结束后会生成 `report.md` 与 `trajectory.json`。

### 3. 非交互迁移

```bash
./bin/rvv-agent migrate <symbol>
./bin/rvv-agent migrate <symbol> --apply
./bin/rvv-agent migrate <symbol> --exec
./bin/rvv-agent migrate <symbol> --apply --exec -j 16
```

参数说明：

- `--apply`：应用补丁到 FFmpeg workspace（否则只落盘到 `runs/`）。
- `--exec`：执行 `configure + make tests/checkasm/checkasm`。
- `--ffmpeg-root`：临时覆盖 `ffmpeg.root`。
- `-j/--jobs`：并行构建线程数，`<=0` 自动取 CPU 核数。

### 4. `plan` 子命令说明

CLI 帮助中存在 `plan` 子命令，但当前代码里 `fixed_plan` 未导入，执行会触发 `NameError`。

```bash
./bin/rvv-agent plan <symbol>
```

当前版本请优先使用 `chat` 或 `migrate`。

## 构建与调试策略

- `BUILD` 阶段：先 `configure`，后 `make tests/checkasm/checkasm`。
- 错误提取：从构建日志中提取关键错误行与尾部上下文。
- `DEBUG` 分类：`compile_error` / `link_error` / `runtime_error` / `test_mismatch`。
- 回滚目标：`locate` / `design` / `generate`，并驱动 `PATCH` 阶段针对性重试。
- 最大调试轮次：`_MAX_DEBUG_CYCLES = 3`。
- 状态机总迭代上限：`_MAX_ITERATIONS = 30`（防止死循环）。
- LLM 调用重试：`core/llm.py` 默认 `max_retries=2`（网络/429/5xx 自动重试）。

## 运行产物说明

每次运行目录：`runs/<task_id>_<symbol>/`

常见文件：

- `user_input.txt`：用户输入（chat 模式）。
- `retrieval_raw.txt`：参考文件筛选原始输出。
- `context.txt`：供分析/生成使用的代码上下文。
- `analysis.json`：语义分析结构化结果。
- `build_log.txt`：构建失败摘要（含多轮追加）。
- `report.md`：运行报告。
- `trajectory.json`：LLM 与 action 轨迹、token 与成本统计。
- `scp_stdout.txt` / `board_stdout.txt`：板端传输和执行输出（启用 board 时）。

状态机持久化目录：`runs/.../state/`

- `task.json`：任务薄清单（当前状态、artifact 索引、回滚提示等）。
- `<STAGE>.json` 或 `<STAGE>/<sub_id>.json`：各阶段 artifact。

## 知识库 (`knowledge_base.json`)

- `patterns`：成功迁移模式（语义 IR、SIMD 策略、架构信息、权重）。
- `errors`：错误模式与修复策略（按计数累计）。
- 在 `KB_UPDATE` 阶段自动更新，运行结束后保存。

## 注意事项

- 交叉编译依赖 RISC-V 工具链，通常需配置 `toolchain.extra_path`。
- 板端执行依赖 SSH；若系统安装 `sshpass` 会优先使用密码模式，否则走原生 `scp/ssh`。
- LLM 生成代码仍需人工审核，尤其是汇编寄存器约束、尾处理与 ABI 细节。
- 当前 README 基于仓库现有实现同步更新，后续改动请同时维护文档。
