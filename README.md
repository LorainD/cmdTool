# rvv-agent

面向 FFmpeg 的 RVV（RISC-V Vector）汇编 SIMD 迁移命令行工具，支持 LLM 辅助的自动化与交互式操作。

## 功能概述

- **意图解析**：理解自然语言指令，自动识别迁移目标算子
- **智能检索**：LLM 辅助定位 C 实现 / x86 / ARM 参考文件及现有 RVV 实现
- **LLM 代码生成**：生成 RVV 汇编（`.S`）、init 注册（`.c`）、Makefile patch，支持增量生成（不覆盖已有 RVV 函数）
- **交互式人机协作**：每个关键决策点（写库、编译、scp、板端运行）均询问确认，会话内只问一次
- **迭代修复**：构建失败时自动提取智能错误摘要，交给 LLM 修复，最多重试 3 次
- **完整可追溯**：每次运行产物落盘到 `runs/<timestamp>_<symbol>/`，含 LLM 轨迹、构建日志、生成文件
- **LLM 断连检测**：网络超时 / 认证失败 / 速率限制时以红字提醒，并给出操作建议
- **远程板卡支持**：可选 sshpass + scp 把 checkasm 传到 RISC-V 硬件板并运行

---

## 目录结构

```
bin/rvv-agent          CLI 入口
rvv_agent/             核心代码
rvv_agent.toml         配置文件
runs/                  每次运行的产物
workplace/FFmpeg/      FFmpeg 工作区（vendor clone）
```

---

## 快速开始

### 1. 配置

编辑 `rvv_agent.toml`：

```toml
[llm]
base_url    = "https://your-llm-endpoint/v1"   # OpenAI-compatible，自动补全 /chat/completions
model       = "gpt-4o"
api_key_env = "LLM_API_KEY"                    # 环境变量名，不要直接写 key
temperature = 0.2

[toolchain]
cross_prefix = "riscv64-unknown-linux-gnu-"
arch         = "riscv64"
target_os    = "linux"
cpu          = "rv64gcv"
extra_cflags = "-march=rv64gcv -mabi=lp64d -O3"
extra_ldflags = "-static"
extra_path   = "/path/to/riscv-toolchain/bin"  # 追加到 PATH

[ffmpeg]
root      = "workplace/FFmpeg"
build_dir = "build"

[board]
enabled    = false   # true 以启用板卡 scp/run
user       = ""
host       = ""
port       = 22
remote_dir = "workplace"
```

设置 API key：

```bash
export LLM_API_KEY='sk-...'
```

### 2. 启动交互式对话（推荐）

```bash
./bin/rvv-agent chat
```

启动后的典型流程：

```
> 迁移 sbrdsp.neg_odd_64 到 RVV

📖 正在生成迁移计划…
Plan：
  1. 意图解析：迁移 sbrdsp.neg_odd_64
  2. 定位 C 实现
  ...

确认按该 plan 继续？[Y/n]

检索/选择出的参考文件：
  libavcodec/aarch64/sbrdsp_neon.S
  [existing-rvv] libavcodec/riscv/sbrdsp_rvv.S  ← 已有 RVV 文件，将增量生成
  ...

确认进入分析/生成阶段？[Y/n]

⚙ 正在分析算子实现…
⚙ 正在调用 LLM 生成 RVV 代码…（可能需要 20–60 秒）

是否把生成文件写入 FFmpeg workspace？[y/N]
是否执行 configure + 构建 checkasm？[y/N]
```

### 3. 自动化迁移

```bash
./bin/rvv-agent migrate ff_vp8_idct16_add          # 只落盘，不写库不编译
./bin/rvv-agent migrate ff_vp8_idct16_add --apply  # 写入 FFmpeg workspace
./bin/rvv-agent migrate ff_vp8_idct16_add --exec   # 同时执行交叉编译
```

### 4. 查看迁移计划

```bash
./bin/rvv-agent plan ff_vp8_idct16_add
```

---

## 错误处理与修复循环

当 `configure` 或 `make checkasm` 失败时：

1. **智能错误提取**：不再简单截取前 N 字节，而是：
   - 提取全部匹配 `error:`、`fatal error:`、`undefined reference`、`make[N]: Error` 等模式的行
   - 取输出末尾 60 行（编译错误通常在最后）
   - 按原始行号合并去重，从尾部截取至 4000 字符送给 LLM
2. **LLM 修复**：最多重试 3 次（configure 和 make 共享计数器）
3. **每轮日志**：全部构建输出追加到 `build_log.txt`，带轮次分隔头

当 LLM 断线时，终端会以**红字**显示分类诊断：

| 错误类型 | 提示 |
|---------|------|
| 网络超时 / 连接失败 | 检查网络 + `base_url` 配置 |
| HTTP 401 / 403 | API key 无效或过期 |
| HTTP 429 / quota | 速率限制，建议等待或换 key |
| key 未设置 | 提示设置对应环境变量 |

---

## 每次运行产物（`runs/<timestamp>_<symbol>/`）

| 文件 | 内容 |
|------|------|
| `user_input.txt` | 用户原始输入及 refine 标记 |
| `intent_raw.txt` | LLM 意图解析原始响应 |
| `retrieval_raw.txt` | 检索过程 + 最终选定文件列表 |
| `context.txt` | 送入分析阶段的代码上下文 |
| `analysis.json` | LLM 结构化分析结果 |
| `discovery.json` | 代码搜索匹配结果 |
| `artifacts/package.json` | 生成的完整补丁包 |
| `artifacts/files/...` | 生成的各文件副本 |
| `build_log.txt` | 全部 configure + make 输出（含多轮迭代，带分隔头） |
| `fix_attempt{N}_*.txt` | LLM 修复原始响应（每轮单独保存） |
| `trajectory.json` | LLM 调用轨迹（tokens / cost / 耗时） |
| `report.md` | 运行概览报告（不含原始构建输出） |
| `board_stdout.txt` | 板端运行输出（启用 board 时） |

---

## 增量生成说明

`.S` 汇编文件**不会被覆盖**：LLM 只输出新增函数，工具自动 append 到文件末尾，防止已有 RVV 实现丢失。

`init.c` 和 `Makefile` 等文本文件以**全量替换**方式写入，LLM 在生成时会收到现有文件内容作为上下文。

---

## 注意事项

- 交叉编译需要配置 RISC-V toolchain（`extra_path` 指向 `bin/`）
- 板卡 scp 使用 sshpass 传递密码，安全性有限，建议改用 SSH key
- LLM 调用会消耗 token，`trajectory.json` 记录每次费用
- 生成的代码仍需人工审核，尤其是复杂算子
