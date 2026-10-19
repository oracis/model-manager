# 模型管理器（Model Manager）

把 Claude Code 的模型切换器、WorkBuddy 的免费 LLM 面板合并成一个带 Tab 的 Tkinter GUI，方便统一管理，并打包成单文件 exe 双击即用。

## 功能

- **Tab 1 · WorkBuddy 免费模型**：抓取 / 评测当前最好用的前 5 个免费 LLM，自动写 `~/.workbuddy/models.json`。带「重新择优 / 先看一眼（只评选不写盘）/ 快速体检 / 撤销改动」四个按钮，每次改动自动备份（最多保留 3 份）。
- **Tab 2 · Claude Code 模型**：以 `~/.claude/profiles/` 为唯一事实来源，一键切换；「＋ 新建模型」会从 Kilo / Zen 公开目录扫描候选，并支持**首字实测测速排序**，不用手打那一长串网关 ID。
- **国际化**：界面支持**中文 / English**。启动自动检测系统 UI 语言（中文环境默认中文，否则英文），主窗口右上角「语言」下拉可手动切换，选择写盘记住（主程序 `~/.workbuddy/model_manager.lang`、切换器 `~/.workbuddy/claude_switcher.lang`）；切换即重启生效。后续加语言只需在对应 `.pyw` 的 `_EN` 表里补一套翻译。

## 两条调用链路

| 类型 | 链路 |
|---|---|
| 普通模型 | Claude Code → ccproxy:`<port>` → 上游 API |
| Zen 免费模型 | Claude Code → ccproxy:3463 → zen_bridge:8787 → opencode serve:41234 → Zen |

WorkBuddy 与 Claude Code 是**两套独立配置**，唯一交集是共享 8787 桥接。

## 文件

| 文件 | 说明 |
|---|---|
| `model_manager.pyw` | 合并后的主程序（Tab 式 GUI），也是桌面 exe 的源码 |
| `claude_model_switcher.pyw` | 独立的 Claude Code 模型切换器（可单独运行，已打包成 `Claude 模型切换器.exe`） |
| `free_llm_panel.pyw` | 独立的免费 LLM 面板（逻辑已并入 Tab1） |
| `zen_bridge_supervisor.pyw` | 无窗口守护进程，常驻探活 8787、单实例锁、登录自启 |
| `build_exe.cmd` | 一键用 PyInstaller 打包成 `模型管理器.exe` |
| `mm.ico` | 图标 |

## 下载预编译 exe（免环境）

不想装 Python/PyInstaller 的，直接到 **[Releases](https://github.com/oracis/model-manager/releases)** 下载：

- `模型管理器.exe`：合并版（WorkBuddy 免费模型 + Claude Code 模型 两个 Tab）
- `Claude 模型切换器.exe`：仅 Claude Code 模型切换

两个都是单文件、Windows 10/11 x64 双击即用，无需安装任何依赖。

## 自己打包（clone 后）

依赖：Python 3.13 + `pip install pyinstaller`。路径全部相对脚本目录，clone 到别处也能用。

```bat
build_exe.cmd
```

一键生成 `dist\model_manager.exe` 与 `dist\claude_switcher.exe` 并复制到桌面（无控制台窗口）。注意 frozen 下 `sys.executable` 指向 exe 自己，脚本内部通过 `_host_python()` 回落到系统 `C:\Python313\python.exe` 来跑 `install_free_llms.py`。

## CI 自动构建

仓库含 `.github/workflows/build-exe.yml`：push 到 `main` 触发 `windows-latest` 自动打包，产物作为 **Actions Artifact** 可直接下载；发布 **GitHub Release** 时，两个 exe 会自动挂到该 Release 附件上（无需本机出包）。

## 版本号管理

版本号唯一来源是仓库根目录的 **`version.py`**（`__version__ = "1.0.0"`）。两个 GUI 的标题栏和状态栏都会显示 `vX.Y.Z`。

- **发布 Release**：打 tag（如 `v1.2.0`），CI 的「Stamp version」步骤会把 tag 去 `v` 后写进 `version.py` 再打包，所以发版 exe 自带精确版本号。
- **push 到 main**：CI 在现有版本后追加 `-dev`（如 `1.0.0-dev`），区分开发构建。
- 本地 `build_exe.cmd` 直接用 `version.py` 里的当前值，无需手动改。

改版本只需动 `version.py`（或打 tag），不用改两个 `.pyw`。

## 候选模型从哪来

「新建模型」的候选来自两家公开、免 key 的模型目录：

- **Kilo**：`https://api.kilo.ai/api/gateway/v1/models`（过滤 `isFree` + 可输出文本，排除 openrouter 聚合别名）
- **Zen**：`https://opencode.ai/zen/v1/models`（过滤 `-free` 结尾或白名单）

下拉列的是「目录里现在有什么」，**不等于快或可用**；要挑最快的请走 Tab1 的「重新择优」（会全量测速排序后自动写配置）。
