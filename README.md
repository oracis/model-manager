# 模型管理器（Model Manager）

把 Claude Code 的模型切换器、WorkBuddy 的免费 LLM 面板合并成一个带 Tab 的 Tkinter GUI，方便统一管理，并打包成单文件 exe 双击即用。

## 功能

- **Tab 1 · WorkBuddy 免费模型**：抓取 / 评测当前最好用的前 5 个免费 LLM，自动写 `~/.workbuddy/models.json`。带「重新择优 / 先看一眼（只评选不写盘）/ 快速体检 / 撤销改动」四个按钮，每次改动自动备份（最多保留 3 份）。
- **Tab 2 · Claude Code 模型**：以 `~/.claude/profiles/` 为唯一事实来源，一键切换；「＋ 新建模型」会从 Kilo / Zen 公开目录扫描候选，并支持**首字实测测速排序**，不用手打那一长串网关 ID。
- **国际化**：界面支持**中文 / English**。启动自动检测系统 UI 语言（中文环境默认中文，否则英文），主窗口右上角「语言」下拉可手动切换，选择写入 `~/.workbuddy/model_manager.lang` 记住；切换即重启生效。后续加语言只需在 `model_manager.pyw` 的 `_EN` 表里补一套翻译。

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
| `claude_model_switcher.pyw` | 独立的 Claude Code 模型切换器（可单独运行） |
| `free_llm_panel.pyw` | 独立的免费 LLM 面板（逻辑已并入 Tab1） |
| `zen_bridge_supervisor.pyw` | 无窗口守护进程，常驻探活 8787、单实例锁、登录自启 |
| `build_exe.cmd` | 一键用 PyInstaller 打包成 `模型管理器.exe` |
| `mm.ico` | 图标 |

## 打包成 exe

```bat
build_exe.cmd
```

产物在 `dist/model_manager.exe`，复制到桌面双击即可（无控制台窗口）。注意 frozen 下 `sys.executable` 指向 exe 自己，脚本内部通过 `_host_python()` 回落到系统 `C:\Python313\python.exe` 来跑 `install_free_llms.py`。

## 候选模型从哪来

「新建模型」的候选来自两家公开、免 key 的模型目录：

- **Kilo**：`https://api.kilo.ai/api/gateway/v1/models`（过滤 `isFree` + 可输出文本，排除 openrouter 聚合别名）
- **Zen**：`https://opencode.ai/zen/v1/models`（过滤 `-free` 结尾或白名单）

下拉列的是「目录里现在有什么」，**不等于快或可用**；要挑最快的请走 Tab1 的「重新择优」（会全量测速排序后自动写配置）。
