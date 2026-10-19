#!/usr/bin/env pythonw
# -*- coding: utf-8 -*-
"""模型管理器 —— 一个窗口管两套模型，Tab 分开。

    [WorkBuddy 免费模型]  免费模型的体检 / 择优 / 撤销（原「免费模型面板」）
    [Claude Code 模型]    Claude Code 的模型切换 / 删除（原「Claude 模型切换器」）

这两个工具改的是**两套完全独立的配置**：
    WorkBuddy    -> ~/.workbuddy/models.json
    Claude Code  -> ~/.claude/settings.json + ~/.claude/profiles/
所以放在一个窗口里只是省得分别点，逻辑上互不干扰。唯一交集是本地
zen_bridge(8787)：两边都可能用到，免费模型那一页测速时会检测并降并发。

启动：C:\\Python313\\pythonw.exe model_manager.pyw
（用系统 Python，因为受管 Python 3.13.12 没带 tkinter）
"""
import http.client
import json
import locale
import os
import re
import shutil
import socket
import subprocess
import sys

# 版本号：唯一来源是仓库根目录的 version.py；CI 出包（尤其 release）会重写它。
# 本地未打包 / 导入失败时回落到 "dev"。
try:
    from version import __version__ as APP_VERSION
except Exception:
    APP_VERSION = "dev"
import threading
import time
import tkinter as tk
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from tkinter import messagebox, ttk

HOME = os.path.expanduser("~")
NO_WINDOW = 0x08000000
DETACHED = 0x00000008

BG = "#ffffff"
FG = "#1f2328"
SUB = "#6a737d"
ACCENT = "#2f6fed"


# ===========================================================================
# 国际化（i18n）
#   中文是源语言：_EN 只放英文翻译表，zh 直接用原文（key 即文案）。
#   自动检测系统 UI 语言；主窗口右上角「语言」下拉可手动改，选择写盘记住。
# ===========================================================================
LANG_FILE = os.path.join(HOME, ".workbuddy", "model_manager.lang")
LANG_NAMES = {"zh": "中文", "en": "English"}
LANG_ORDER = ["zh", "en"]

_EN = {
    "语言": "Language",
    "帮助": "Help",
    "关于": "About",
    "本机版本：": "Local version: ",
    "源码与更新：": "Source & updates: ",
    "合并管理 WorkBuddy 免费模型与 Claude Code 模型的小工具。": "A small tool that manages WorkBuddy free models and Claude Code models in one window.",
    "模型管理器": "Model Manager",
    "WorkBuddy 免费模型": "WorkBuddy Free Models",
    "Claude Code 模型": "Claude Code Models",
    "模型 ID": "Model ID",
    "来源": "Source",
    "方式": "Mode",
    "#": "#",
    "重新择优": "Repick",
    "先看一眼": "Preview",
    "快速体检": "Health Check",
    "撤销改动": "Undo",
    "就绪": "Ready",
    "运行中…": "Running…",
    "从未": "Never",
    "未知": "Unknown",
    "提示：改完配置后需要重启 WorkBuddy 才会读到新模型列表。\n":
        "Note: restart WorkBuddy after changing config to reload the model list.\n",
    "只改 WorkBuddy 的 models.json，不动 Claude Code 的 settings.json / profiles。":
        "Only edits WorkBuddy's models.json; does not touch Claude Code's settings.json / profiles.",
    "；测速会自动降到 3 并发，避免挤占它的请求。":
        "; benchmarks auto-drop to 3 concurrent to avoid starving its requests.",
    "巡检 %s · 重选 %s": "Checked %s · Repicked %s",
    "Claude Code：%s（直连，不受影响）": "Claude Code: %s (direct, not affected)",
    "Claude Code：%s（经同一桥接，测速时会排队）": "Claude Code: %s (shares the bridge; benchmarks will queue)",
    "Claude Code：%s（本地代理，不共用桥接）": "Claude Code: %s (local proxy, no shared bridge)",
    "没有可用的备份（%s 下找不到 models.json.bak-*）。\n":
        "No usable backup (no models.json.bak-* under %s).\n",
    "最近的备份读不出来或不是模型列表：%s\n":
        "Latest backup unreadable or not a model list: %s\n",
    "恢复失败：%s\n": "Restore failed: %s\n",
    "已恢复到 %s（%d 个模型）。\n": "Restored to %s (%d models).\n",
    "顺手清掉 %d 份旧备份（只留最近 %d 份）。\n":
        "Cleaned up %d old backups (keeping the last %d).\n",
    "撤销前的配置另存为：%s\n": "Pre-undo config saved as: %s\n",
    "重启 WorkBuddy 后生效。\n": "Takes effect after restarting WorkBuddy.\n",
    "恢复到 %s 的备份？\n\n共 %d 个模型：\n%s%s\n\n当前配置会先另存一份，撤销本身也可回退。":
        "Restore the backup from %s?\n\n%d models total:\n%s%s\n\nCurrent config is saved first; the undo itself is reversible.",
    "%s中…": "%s…",
    "Claude Code 正在用同一桥接，本次并发降到 %s 路。\n\n":
        "Claude Code is using the same bridge; concurrency dropped to %s this run.\n\n",
    "执行失败：%s\n": "Execution failed: %s\n",
    "\n✓ %s完成\n": "\n✓ %s done\n",
    "\n× %s结束，退出码 %d\n": "\n× %s finished, exit code %d\n",
    "上面就是新的前 5 名。想要就点「重新择优」，配置一个字节都没动。\n":
        "Above are the new top 5. Click \"Repick\" to apply; config is untouched.\n",
    "点哪个模型，就切到哪个。切完重启 Claude Code 即可":
        "Click a model to switch to it. Restart Claude Code after switching.",
    "删除模型（免费模型真删·付费归档可恢复）":
        "Delete model (free: hard delete · paid: archived & recoverable)",
    "刷新列表": "Refresh",
    "＋ 新建模型": "+ New Model",
    "删除所选模型": "Delete Selected",
    "新建模型（Claude Code）": "New Model (Claude Code)",
    "从可用免费模型里选（点一行自动填到下面）":
        "Pick from available free models (click a row to fill it below)",
    "刷新目录": "Refresh List",
    "测速排序": "Benchmark Sort",
    "「测速排序」逐个量首字耗时（约半分钟），灰=未测 红=挂":
        "\"Benchmark Sort\" measures first-token latency per model (~30s). Gray=untested, Red=down",
    "上下文": "Context",
    "已装": "Installed",
    "首字实测": "1st-token",
    "超时/挂": "Timeout/Down",
    "正在拉 Kilo / Zen 目录…": "Fetching Kilo / Zen catalog…",
    "共 %d 个": "%d found",
    "没扫到（网络问题？可手填）": "Nothing found (network issue? type manually)",
    "目录还是空的，先「刷新目录」": "Catalog is empty; click \"Refresh List\" first",
    "测速完成 %d/%d 有响应，已按快慢排": "Benchmark done: %d/%d responded, sorted by speed",
    "扫描中": "Scanning",
    "测速中 %d/%d": "Benchmarking %d/%d",
    "正在拉 Kilo / Zen 目录…": "Fetching Kilo / Zen catalog…",
    "模型 ID（也可以直接手写）": "Model ID (or type manually)",
    "→ 走 Kilo 网关直连（ID 带斜杠）": "→ Kilo gateway direct (ID has a slash)",
    "→ 走 Zen 免费，经本地桥接 8787": "→ Zen free, via local bridge 8787",
    "本地代理端口": "Local proxy port",
    "（自动挑一个没占用的）": "(auto-picks a free one)",
    "取消": "Cancel",
    "创建并切换": "Create & Switch",
    "缺模型 ID": "Missing Model ID",
    "先填模型 ID": "Fill in the model ID first",
    "端口不对": "Invalid port",
    "端口要填数字": "Port must be a number",
    "创建失败": "Creation failed",
    "没有选中": "Nothing selected",
    "先在下拉框里选一个要删除的模型": "Select a model to delete from the dropdown first",
    "读不了这个 profile": "Cannot read this profile",
    "将【永久删除】免费模型「%s」\nprofile 文件: %s\n上游模型 ID: %s\n\n免费模型直接真删，不留 backup。\n确定删除？":
        "Will PERMANENTLY delete free model \"%s\"\nprofile file: %s\nupstream model ID: %s\n\nFree models are hard-deleted (no backup).\nDelete?",
    "将删除模型「%s」\nprofile 文件: %s\n上游模型 ID: %s\n\n操作 = 把该 profile 移入\n~/.claude/backups/deleted/\n（不是硬删，需要时可捡回）\n\n确定删除？":
        "Will delete model \"%s\"\nprofile file: %s\nupstream model ID: %s\n\nAction = move the profile to\n~/.claude/backups/deleted/\n(not a hard delete; recoverable)\n\nDelete?",
    "确认删除": "Confirm Deletion",
    "删除失败": "Deletion failed",
    "「%s」已永久删除（免费模型，无 backup）。": "\"%s\" permanently deleted (free model, no backup).",
    "「%s」已移除。\n归档到：%s（可恢复）": "\"%s\" removed.\nArchived to: %s (recoverable)",
    "已删除": "Deleted",
    "重启 Claude Code 后，切换器列表不再显示它。":
        "After restarting Claude Code, the switcher list won't show it.",
    "\n\n注意：这是当前正在用的模型。settings.json 未改动，但已无 profile 与它对应，建议再点一个别的模型。":
        "\n\nNote: this is the model currently in use. settings.json is unchanged, but no profile matches it now; pick another model.",
    "当前：%s\n切换后重启 Claude Code；想保留上下文重启时加 --continue":
        "Current: %s\nRestart Claude Code after switching; use --continue to keep context",
    "正在拉起 Zen 桥接（约 30 秒，含 opencode serve 冷启动）...":
        "Starting Zen bridge (~30s, includes opencode serve cold start)...",
    "正在切换...": "Switching...",
    "该模型已删除": "Model deleted",
    "「%s」的 profile 已被删除。\n点下方「刷新列表」或重启本工具。":
        "\"%s\"'s profile was deleted.\nClick \"Refresh\" below or restart this tool.",
    "已自动连上 %s（%s）": "Auto-connected to %s (%s)",
    "自动连接失败：%s": "Auto-connect failed: %s",
    "\n\n桥接已就绪，WorkBuddy 侧也能用同一个模型：\n模型列表选 vendor=opencode 的 %s":
        "\n\nBridge ready; WorkBuddy can use the same model:\npick vendor=opencode %s in the model list",
    "已切换": "Switched",
    "部分失败": "Partial failure",
    "已切换到：%s\n%s%s\n\n重启 Claude Code 后生效。\n想接着之前的对话继续：重启时用 claude --continue\n（换模型不会丢会话，历史都在 ~/.claude/projects/）":
        "Switched to: %s\n%s%s\n\nTakes effect after restarting Claude Code.\nTo continue the conversation: restart with claude --continue\n(switching models won't lose the session; history is in ~/.claude/projects/)",
    "未安装、已从列表隐藏：%s": "Not installed, hidden from list: %s",
    "（直连，无需代理）": "(Direct, no proxy)",
    "代理已在运行": "Proxy already running",
    "本地代理 %d 未运行，且该模型无登记启动参数，未自动拉起；请先手动启动对应代理":
        "Local proxy %d not running and this model has no registered launch args; not auto-started. Start the proxy manually first",
    "已启动代理": "Proxy started",
    "代理启动超时，见 ~/.claude/ccproxy-%d.log": "Proxy start timed out; see ~/.claude/ccproxy-%d.log",
    "桥接已在运行": "Bridge already running",
    "桥接启动超时，见 ~/.claude/zen-bridge.log": "Bridge start timed out; see ~/.claude/zen-bridge.log",
    "桥接已启动": "Bridge started",
    "桥接端口已开但健康检查未通过，见 ~/.claude/zen-bridge.log":
        "Bridge port open but health check failed; see ~/.claude/zen-bridge.log",
    "失败：%s": "Failed: %s",
    "模型 ID 里没有可用字符": "No usable characters in model ID",
    "已经有同名 profile：%s.json": "Profile with same name already exists: %s.json",
    "由模型管理器新建（%s）": "Created by Model Manager (%s)",
    "Zen 桥接": "Zen bridge",
    "Kilo 直连": "Kilo direct",
    # 模型显示名里的注解
    "（免费·Zen）": "(Free·Zen)",
    "（免费·Zen·1M·实测最快）": "(Free·Zen·1M·Fastest)",
    "（免费·Zen·256k·输出64k）": "(Free·Zen·256k·out 64k)",
    "（免费·Zen·1M）": "(Free·Zen·1M)",
    "（免费·Zen·200k）": "(Free·Zen·200k)",
    "（免费）": "(Free)",
    "（免费·可看图）": "(Free·can see images)",
    "（免费·agent首选）": "(Free·agent pick)",
    "（付费）": "(Paid)",
    "（免费·稳定）": "(Free·stable)",
    "（免费+读图）": "(Free+Vision)",
    # 删除下拉短标签
    "DS V4.1 Flash 免费·Zen": "DS V4.1 Flash Free·Zen",
    "👑 Space Bunny 免费·最快": "👑 Space Bunny Free·Fastest",
    "LL Alpha 免费·256k": "LL Alpha Free·256k",
    "Nemotron VL 免费·1M": "Nemotron VL Free·1M",
    "LongCat 免费·1M": "LongCat Free·1M",
    "MiMo V2.6 免费": "MiMo V2.6 Free",
    "Big Pickle 免费": "Big Pickle Free",
    "DeepSeek-V4 付费": "DeepSeek-V4 Paid",
    "GLM-4-Flash 免费": "GLM-4-Flash Free",
    "GLM-4-Flash 免费+读图": "GLM-4-Flash Free+Vision",
    "GLM-4.1V 思考 免费": "GLM-4.1V Think Free",
    "GLM-4.6V 免费": "GLM-4.6V Free",
    "Kilo 文本 免费+读图": "Kilo Text Free+Vision",
    "Kilo LFM2.5 免费": "Kilo LFM2.5 Free",
    "读图 Vision 免费": "Vision Free",
    "Kilo 文本 + 读图桥接（免费）": "Kilo Text + Vision Bridge (Free)",
    "读图 Vision（免费·可看图）": "Vision (Free·can see images)",
    "Kilo 快稳 LFM2.5（免费·agent首选）": "Kilo Fast LFM2.5 (Free·agent pick)",
    "GLM-4-Flash + 读图桥接（免费）": "GLM-4-Flash + Vision Bridge (Free)",
    "DeepSeek V4.1 Flash（免费·Zen）": "DeepSeek V4.1 Flash (Free·Zen)",
    "👑 Space Bunny Free（免费·Zen·1M·实测最快）": "👑 Space Bunny Free (Free·Zen·1M·Fastest)",
    "LL Alpha Free（免费·Zen·256k·输出64k）": "LL Alpha Free (Free·Zen·256k·out 64k)",
    "Nemotron 3.5 Super-VL（免费·Zen·1M）": "Nemotron 3.5 Super-VL (Free·Zen·1M)",
    "LongCat 2.5 Preview（免费·Zen·1M）": "LongCat 2.5 Preview (Free·Zen·1M)",
    "MiMo V2.6 Flash（免费·Zen·200k）": "MiMo V2.6 Flash (Free·Zen·200k)",
    "Big Pickle（免费·Zen·200k）": "Big Pickle (Free·Zen·200k)",
    "DeepSeek-V4 (付费)": "DeepSeek-V4 (Paid)",
    "GLM-4-Flash（免费·稳定）": "GLM-4-Flash (Free·stable)",
}


def _detect_lang():
    """检测系统 UI 语言：Windows 读 GetUserDefaultUILanguage，其他回退 locale。"""
    try:
        lid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        if (lid & 0x3FF) == 0x04:          # 主语言 0x04 = 中文（各变体）
            return "zh"
    except Exception:
        pass
    try:
        loc = (locale.getdefaultlocale() or ("",))[0] or ""
        if loc.lower().startswith("zh"):
            return "zh"
    except Exception:
        pass
    return "en"


def load_lang():
    try:
        with open(LANG_FILE, encoding="utf-8") as f:
            v = f.read().strip()
        return v if v in LANG_ORDER else None
    except Exception:
        return None


def save_lang(code):
    try:
        os.makedirs(os.path.dirname(LANG_FILE), exist_ok=True)
        with open(LANG_FILE, "w", encoding="utf-8") as f:
            f.write(code)
    except Exception:
        pass


_current_lang = load_lang() or _detect_lang()


def t(key, *args):
    """取当前语言的文案。中文即源语言（key 本身）；英文查 _EN 表，查不到回落 key。"""
    if not key:
        return key
    d = _LANGS_EN if _current_lang == "en" else None
    s = d.get(key, key) if d is not None else key
    if args:
        try:
            return s % args
        except Exception:
            return s
    return s


# _LANGS_EN 别名，方便 t() 内部引用
_LANGS_EN = _EN


# ===========================================================================
# 共用小工具
# ===========================================================================
def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def fmt_ts(ts):
    if not ts:
        return t("从未")
    return time.strftime("%m-%d %H:%M", time.localtime(ts))


# ===========================================================================
# Tab 1：WorkBuddy 免费模型
# ===========================================================================
MODELS_JSON = os.path.join(HOME, ".workbuddy", "models.json")
AUTO_STATE = os.path.join(HOME, ".workbuddy", "free_llm_auto_state.json")
LLM_SCRIPT = os.path.join(HOME, ".workbuddy", "skills", "free-llm-installer",
                          "scripts", "install_free_llms.py")
CLAUDE_SETTINGS = os.path.join(HOME, ".claude", "settings.json")
BACKUP_DIR = os.path.join(HOME, ".workbuddy", "backups")
MAX_BACKUPS = 3          # models.json 备份只留最近几份


def _host_python():
    """返回一个「能跑 .py 脚本」的解释器路径。

    打包成 exe 之后 sys.executable 指向 model_manager.exe，拿它去
    subprocess 跑 install_free_llms.py 只会再弹一个自己（还带一堆看不懂的
    参数），所以必须回落到真正的 python.exe。这里优先带 tkinter 的系统
    Python，其次受管 Python（跑纯标准库脚本够用）。
    """
    if not getattr(sys, "frozen", False):
        return sys.executable
    cands = [
        r"C:\Python313\python.exe",
        r"C:\Users\DELL\.workbuddy\binaries\python\versions\3.13.12\python.exe",
    ]
    for p in cands:
        if os.path.isfile(p):
            return p
    return "python"


LLM_PY = _host_python()  # 跑安装脚本用


def claude_hint():
    """看 Claude Code 现在用的是哪个模型、是否经过共享的 8787 桥接。

    免费模型页只改 ~/.workbuddy/models.json，和 Claude Code 的配置是两套，
    但两边可能共用同一个本地桥接进程，所以这里要把「会互相挤」的情况说清楚。

    返回 (text, via_bridge)；没装/没配置时 text 为空串。
    """
    s = read_json(CLAUDE_SETTINGS)
    if not isinstance(s, dict):
        return "", False
    env = s.get("env") or {}
    base = str(env.get("ANTHROPIC_BASE_URL") or "")
    model = str(s.get("model") or env.get("ANTHROPIC_MODEL") or "")

    if not (("127.0.0.1" in base) or ("localhost" in base)):
        return t("Claude Code：%s（直连，不受影响）", model or "官方"), False

    # 本地端口 → 一定是 ccproxy。再看它转发到哪：profiles 里带 x_bridge
    # 标记、或 x_ccproxy_args 的 upstream 指向 8787，才是共用桥接。
    via = False
    pdir = os.path.join(HOME, ".claude", "profiles")
    if os.path.isdir(pdir):
        for fn in os.listdir(pdir):
            if not fn.endswith(".json"):
                continue
            p = read_json(os.path.join(pdir, fn))
            if not isinstance(p, dict):
                continue
            penv = p.get("env") or {}
            if str(penv.get("ANTHROPIC_BASE_URL") or "") != base:
                continue
            args = " ".join(str(a) for a in (p.get("x_ccproxy_args") or []))
            via = bool(p.get("x_bridge")) or "8787" in args
            break
    if via:
        return t("Claude Code：%s（经同一桥接，测速时会排队）", model), True
    return t("Claude Code：%s（本地代理，不共用桥接）", model), False


def latest_backup():
    """最近一次自动改动前的备份 → (路径, 时间描述)；没有就 (None, "")。

    只认 `models.json.bak-*`，且跳过两类：
      - `bak-before-undo-*`：撤销前自己存的，用它会让「撤销」来回打转
      - `bak-used-*`：已经被撤销用过的，连着撤两次要能退到更早那份
    """
    if not os.path.isdir(BACKUP_DIR):
        return None, ""
    cands = []
    for fn in os.listdir(BACKUP_DIR):
        if not fn.startswith("models.json.bak-"):
            continue
        if "before-undo" in fn or "used" in fn:
            continue
        p = os.path.join(BACKUP_DIR, fn)
        try:
            cands.append((os.path.getmtime(p), p))
        except OSError:
            continue
    if not cands:
        return None, ""
    ts, p = max(cands)
    return p, time.strftime("%m-%d %H:%M:%S", time.localtime(ts))


def mark_used(path):
    """把用过的备份改名，避免下一次撤销又选中同一份。"""
    try:
        fn = os.path.basename(path)
        ts = fn[-15:] if len(fn) >= 15 else time.strftime("%Y%m%d-%H%M%S")
        dst = os.path.join(os.path.dirname(path), "models.json.bak-used-%s" % ts)
        if os.path.exists(dst):
            dst = "%s-%d" % (dst, int(time.time() % 1000))
        os.replace(path, dst)
        return dst
    except OSError:
        return path


def prune_backups(keep=MAX_BACKUPS):
    """只留最近 keep 份 models.json 备份。免费模型换得勤，攒几十份没意义。"""
    if not os.path.isdir(BACKUP_DIR):
        return 0
    items = []
    for fn in os.listdir(BACKUP_DIR):
        if not fn.startswith("models.json.bak-"):
            continue
        p = os.path.join(BACKUP_DIR, fn)
        try:
            items.append((os.path.getmtime(p), p))
        except OSError:
            continue
    items.sort(reverse=True)
    n = 0
    for _, p in items[keep:]:
        try:
            os.remove(p)
            n += 1
        except OSError:
            pass
    return n


class FreeLLMFrame(tk.Frame):
    """WorkBuddy 的免费模型页。"""

    def __init__(self, master):
        super().__init__(master, bg=BG)
        self.running = False
        self.claude_via_bridge = False
        self.mode = ""

        pad = {"padx": 14}

        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", pady=(14, 6), **pad)
        tk.Label(head, text=t("WorkBuddy 免费模型"), font=("Microsoft YaHei UI", 15, "bold"),
                 bg=BG, fg=FG).pack(side="left")
        self.meta = tk.Label(head, text="", font=("Microsoft YaHei UI", 9),
                             bg=BG, fg=SUB)
        self.meta.pack(side="right")

        # 影响范围提示：只写 WorkBuddy 的 models.json，Claude Code 是另一套配置
        self.scope = tk.Label(self, text="", font=("Microsoft YaHei UI", 9),
                              bg=BG, fg=SUB, anchor="w", justify="left")
        self.scope.pack(fill="x", pady=(0, 6), **pad)

        # 当前模型表
        wrap = tk.Frame(self, bg=BG)
        wrap.pack(fill="both", expand=False, **pad)
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("T.Treeview", background=BG, fieldbackground=BG,
                        foreground=FG, rowheight=26, borderwidth=0)
        style.configure("T.Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"))
        self.tree = ttk.Treeview(wrap, columns=("n", "id", "vendor", "mode"),
                                 show="headings", height=7, style="T.Treeview")
        for col, txt, w, anchor in (("n", t("#"), 36, "center"),
                                    ("id", t("模型 ID"), 480, "w"),
                                    ("vendor", t("来源"), 150, "w"),
                                    ("mode", t("方式"), 80, "center")):
            self.tree.heading(col, text=txt)
            self.tree.column(col, width=w, anchor=anchor)
        self.tree.pack(fill="x")

        # 按钮
        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", pady=10, **pad)

        def mkbtn(text, cmd, primary=False):
            b = tk.Button(bar, text=text, command=cmd,
                          font=("Microsoft YaHei UI", 10),
                          relief="flat", cursor="hand2",
                          bg=ACCENT if primary else "#eef0f3",
                          fg="#ffffff" if primary else FG,
                          # 运行中会禁用按钮。tk 默认把文字刷成系统灰，
                          # 蓝底上灰字几乎看不见（实测就是这个原因）。
                          disabledforeground="#edf2fe" if primary else "#9aa1a9",
                          activebackground="#2559c9" if primary else "#e2e5e9",
                          activeforeground="#ffffff" if primary else FG,
                          padx=16, pady=6)
            b.pack(side="left", padx=(0, 10))
            return b

        self.btn_pick = mkbtn(t("重新择优"), lambda: self.run("repick"), True)
        self.btn_preview = mkbtn(t("先看一眼"), lambda: self.run("preview"))
        self.btn_check = mkbtn(t("快速体检"), lambda: self.run("check"))
        self.btn_undo = mkbtn(t("撤销改动"), self.undo)
        self.status = tk.Label(bar, text=t("就绪"), font=("Microsoft YaHei UI", 9),
                               bg=BG, fg=SUB)
        self.status.pack(side="right")
        self.ver = tk.Label(bar, text="v" + APP_VERSION, font=("Microsoft YaHei UI", 9),
                            bg=BG, fg=SUB)
        self.ver.pack(side="left")

        # 日志
        self.log = tk.Text(self, height=12, relief="flat", bg="#f6f8fa", fg=FG,
                           font=("Consolas", 9), padx=10, pady=8, wrap="word")
        self.log.pack(fill="both", expand=True, pady=(0, 12), **pad)
        self.log.tag_config("dim", foreground=SUB)
        self.log.tag_config("ok", foreground="#1a7f37")
        self.log.tag_config("warn", foreground="#bf8700")
        self.log.configure(state="disabled")

        self.refresh_table()
        self.write(t("提示：改完配置后需要重启 WorkBuddy 才会读到新模型列表。\n"),
                   "dim")

    # ---------------- UI ----------------
    def write(self, text, tag=None):
        self.log.configure(state="normal")
        self.log.insert("end", text, tag or ())
        self.log.see("end")
        self.log.configure(state="disabled")

    def refresh_scope(self):
        """刷新「影响范围」那行：这页改的是什么、会不会碰到 Claude Code。"""
        txt, via = claude_hint()
        self.claude_via_bridge = via
        line = t("只改 WorkBuddy 的 models.json，不动 Claude Code 的 settings.json / profiles。")
        if txt:
            line += "\n" + txt
            if via:
                line += t("；测速会自动降到 3 并发，避免挤占它的请求。")
        self.scope.configure(text=line)

    def refresh_table(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        models = read_json(MODELS_JSON) or []
        n = 0
        for m in models:
            if not isinstance(m, dict):
                continue
            url = str(m.get("url") or "")
            mode = "桥接" if "127.0.0.1" in url else "直连"
            vendor = str(m.get("vendor") or "")
            if vendor == "DeepSeek":
                mode = "直连"
            n += 1
            self.tree.insert("", "end", values=(n, m.get("id"), vendor, mode))
        st = read_json(AUTO_STATE) or {}
        self.meta.configure(text=t("巡检 %s · 重选 %s",
                            fmt_ts(st.get("last_check_ts")),
                            fmt_ts(st.get("last_pick_ts"))))
        self.refresh_scope()

    def set_running(self, flag, text=""):
        self.running = flag
        for b in (self.btn_pick, self.btn_preview, self.btn_check, self.btn_undo):
            b.configure(state="disabled" if flag else "normal",
                        cursor="arrow" if flag else "hand2")
        self.status.configure(text=text or (t("运行中…") if flag else t("就绪")))

    # ---------------- 撤销 ----------------
    def undo(self):
        """把 models.json 恢复到最近一次自动改动之前的状态。

        为什么要有：现在配置全由脚本生成（url / apiKey / 能力位都是算出来的），
        手工改 JSON 既不现实也容易改坏，所以「改错了怎么办」必须由程序兜住。
        """
        bk, when = latest_backup()
        if not bk:
            self.write(t("没有可用的备份（%s 下找不到 models.json.bak-*）。\n")
                       % BACKUP_DIR, "warn")
            return
        data = read_json(bk)
        if not isinstance(data, list):
            self.write(t("最近的备份读不出来或不是模型列表：%s\n") % bk, "warn")
            return
        names = "、".join(str(m.get("id")) for m in data[:6] if isinstance(m, dict))
        if not messagebox.askyesno(
                t("撤销改动"),
                t("恢复到 %s 的备份？\n\n共 %d 个模型：\n%s%s\n\n当前配置会先另存一份，撤销本身也可回退。")
                % (when, len(data), names, "…" if len(data) > 6 else ""),
                parent=self):
            return
        try:
            # 先把当前存一份，避免「撤销」变成单向操作
            safe = "%s.bak-before-undo-%s" % (MODELS_JSON, time.strftime("%Y%m%d-%H%M%S"))
            if os.path.isfile(MODELS_JSON):
                shutil.copyfile(MODELS_JSON, safe)
            shutil.copyfile(bk, MODELS_JSON)
        except Exception as e:
            self.write(t("恢复失败：%s\n") % e, "warn")
            return
        mark_used(bk)                       # 这份用过了，下次撤销往前推一份
        pruned = prune_backups()
        self.refresh_table()
        self.write(t("已恢复到 %s（%d 个模型）。\n") % (when, len(data)), "ok")
        if pruned:
            self.write(t("顺手清掉 %d 份旧备份（只留最近 %d 份）。\n")
                       % (pruned, MAX_BACKUPS), "dim")
        self.write(t("撤销前的配置另存为：%s\n") % os.path.basename(safe), "dim")
        self.write(t("重启 WorkBuddy 后生效。\n"), "dim")

    # ---------------- 执行 ----------------
    def run(self, mode):
        if self.running:
            return
        # Claude Code 正走同一桥接时限流降到 3：桥接内部并发上限是 4，
        # 本页开 6 路会把它的对话请求挤到队尾。只测 WorkBuddy 时不需要让。
        jobs = "3" if self.claude_via_bridge else "6"
        if mode == "repick":
            args = [LLM_PY, LLM_SCRIPT, "--auto", "--repick",
                    "--bench-jobs", jobs, "--bench-timeout", "60"]
            title = "重新择优"
        elif mode == "preview":
            # --pick --dry-run：完整测速+验工具，排出前 5，但一个字节都不写
            args = [LLM_PY, LLM_SCRIPT, "--pick", "--dry-run", "--top", "5",
                    "--bench-jobs", jobs, "--bench-timeout", "60"]
            title = "预览"
        else:
            args = [LLM_PY, LLM_SCRIPT, "--auto", "--force",
                    "--bench-jobs", jobs, "--bench-timeout", "60"]
            title = "体检"
        self.mode = mode
        self.set_running(True, t("%s中…", title))
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.write("$ install_free_llms.py %s\n\n" % " ".join(args[2:]), "dim")
        if self.claude_via_bridge:
            self.write(t("Claude Code 正在用同一桥接，本次并发降到 %s 路。\n\n")
                       % jobs, "dim")
        threading.Thread(target=self._worker, args=(args, title),
                         daemon=True).start()

    def _worker(self, args, title):
        try:
            p = subprocess.Popen(args, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True,
                                 encoding="utf-8", errors="replace",
                                 creationflags=NO_WINDOW, bufsize=1)
            for line in p.stdout:
                self.after(0, self.write, line)
            p.wait()
            rc = p.returncode
        except Exception as e:
            self.after(0, self.write, t("执行失败：%s\n") % e)
            rc = -1
        self.after(0, self._done, title, rc)

    def _done(self, title, rc):
        self.set_running(False)
        self.refresh_table()
        if rc == 0:
            self.write(t("\n✓ %s完成\n") % title, "ok")
        else:
            self.write(t("\n× %s结束，退出码 %d\n") % (title, rc), "warn")
        # 预览不改配置，别提示重启；其余两种都可能动过 models.json
        if self.mode == "preview":
            if rc == 0:
                self.write(t("上面就是新的前 5 名。想要就点「重新择优」，"
                           "配置一个字节都没动。\n"), "dim")
        else:
            self.write(t("重启 WorkBuddy 后生效。\n"), "dim")


# ===========================================================================
# Tab 2：Claude Code 模型切换
# ===========================================================================
CLAUDE_PY = r"C:\Users\DELL\.workbuddy\binaries\python\versions\3.13.12\python.exe"
CC = r"C:\Users\DELL\.workbuddy\skills\claude-code-third-party-model\scripts\ccproxy.py"
ZB = r"C:\Users\DELL\.workbuddy\skills\claude-code-third-party-model\scripts\zen_bridge.py"
ZB_PORT = 8787
ZB_SDK_PORT = 41234
KILO_UPSTREAM = "https://api.kilo.ai/api/gateway/v1/chat/completions"

# 模型目录（免 key 公开接口）——新建模型时的候选来源
KILO_MODELS_URL = "https://api.kilo.ai/api/gateway/v1/models"
ZEN_MODELS_URL = "https://opencode.ai/zen/v1/models"
# Zen 少数免费模型 id 不带 -free 后缀，只能靠白名单认
ZEN_STEALTH_FREE = ("big-pickle",)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def scan_candidates(timeout=25):
    """扫 Kilo / Zen 两家的免费模型目录，返回 [(id, 来源, 上下文k)]。

    过滤口径与免费模型页保持一致，避免把不能当聊天模型用的东西列出来：
      Kilo —— isFree（比 pricing==0 可靠，lyria 定价 0 但按首收费）
              + output_modalities 含 text（排除音乐/绘图模型）
      Zen  —— id 以 -free 结尾，或在白名单里
    两家里任一失败就只返回另一家的结果，不整体报错。
    """
    out = []
    # Kilo 直连：本机那个 http_proxy 常年失效，绕开它更快
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(KILO_MODELS_URL, headers={"User-Agent": UA})
        with op.open(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        for m in data.get("data") or []:
            if not m.get("isFree"):
                continue
            arch = m.get("architecture") or {}
            if "text" not in (arch.get("output_modalities") or []):
                continue
            mid = str(m.get("id") or "").strip()
            if not mid or "openrouter" in mid.lower():
                continue        # 聚合路由别名，首选模型每轮不同
            out.append((mid, "Kilo", int((m.get("context_length") or 0) // 1000)))
    except Exception:
        pass
    try:
        req = urllib.request.Request(ZEN_MODELS_URL, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        for m in data.get("data") or []:
            mid = str(m.get("id") or "").strip()
            if mid and (mid.endswith("-free") or mid in ZEN_STEALTH_FREE):
                out.append((mid, "Zen", 0))
    except Exception:
        pass
    return out


# ---- 候选模型测速 ----------------------------------------------------------
# 目录只告诉你「有什么」，不告诉你「快不快」。这里对候选逐个发一条
# 小请求（流式），量「首字耗时」，结果缓存到磁盘，下次打开对话框直接显示。
# 链路口径与 models.json 完全一致：
#   带斜杠  -> Kilo 网关直连（apiKey anonymous）
#   不带    -> Zen 免费，走本地桥接 8787（桥接没起则全部失败，界面会提示）
CAND_BENCH_PATH = os.path.join(HOME, ".workbuddy", "free_llm_candidate_bench.json")


def load_bench_cache():
    try:
        with open(CAND_BENCH_PATH, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_bench_cache(d):
    try:
        tmp = CAND_BENCH_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, CAND_BENCH_PATH)
    except Exception:
        pass


def _bench_one(mid, timeout=20):
    """单模型首字耗时（秒）。失败/超时返回 None。"""
    if "/" in mid:
        url, key = KILO_UPSTREAM, "anonymous"
    else:
        url = "http://127.0.0.1:%d/v1/chat/completions" % ZB_PORT
        key = "sk-local"
    body = json.dumps({
        "model": mid, "stream": True, "max_tokens": 16,
        "messages": [{"role": "user", "content": "只回复一个字：好"}],
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + key,
        "User-Agent": UA,
    })
    t0 = time.time()
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with op.open(req, timeout=timeout) as r:
            ttft = None
            for raw in r:                       # 读完整个流，避免对端断管
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    obj = json.loads(payload)
                except Exception:
                    continue                    # 心跳/非 JSON 帧忽略
                if obj.get("error"):
                    # 桥接会先发一帧假的 role 块再报错，不能只看第一帧
                    return None
                if ttft is None and obj.get("choices"):
                    ttft = time.time() - t0
        return round(ttft, 2) if ttft is not None else None
    except Exception:
        return None


def bench_candidates(cands, jobs=6, timeout=20, progress=None):
    """并发实测候选列表，返回 {模型id: {"ttft": 秒或None, "ts": 时间}}。"""
    cache = load_bench_cache()
    total = len(cands)
    with ThreadPoolExecutor(max_workers=jobs) as ex:
        futs = {ex.submit(_bench_one, mid, timeout): mid
                for mid, _src, _ctx in cands}
        for i, fut in enumerate(as_completed(futs)):
            mid = futs[fut]
            try:
                ttft = fut.result()
            except Exception:
                ttft = None
            cache[mid] = {"ttft": ttft,
                          "ts": time.strftime("%Y-%m-%d %H:%M")}
            if progress:
                progress(i + 1, total, mid, ttft)
    save_bench_cache(cache)
    return cache


def _auth_token():
    """从现有 profile 里借一个 ANTHROPIC_AUTH_TOKEN。

    这个 token 只对本地 ccproxy 生效（ccproxy 用自己的 key 打上游），
    值是什么无所谓，但没有它 Claude Code 会当成未配置。
    """
    for fn in sorted(os.listdir(PROFILES_DIR) or []) if os.path.isdir(PROFILES_DIR) else []:
        if not fn.endswith(".json"):
            continue
        p = read_json(os.path.join(PROFILES_DIR, fn))
        tok = ((p or {}).get("env") or {}).get("ANTHROPIC_AUTH_TOKEN")
        if tok:
            return tok
    return "anonymous"


# name=显示名, profile=profiles 下文件名(无后缀), port=本地 ccproxy 端口(None=直连),
# args=ccproxy 启动参数, bridge=是否需要先拉起 zen_bridge
MODELS = [
    (t("DeepSeek V4.1 Flash（免费·Zen）"), "zen-ds-flash", 3462,
     "--upstream http://127.0.0.1:8787/v1/chat/completions "
     "--model deepseek-v4.1-flash-free --key anonymous", True),
    (t("👑 Space Bunny Free（免费·Zen·1M·实测最快）"), "zen-space-bunny", 3463,
     "--upstream http://127.0.0.1:8787/v1/chat/completions "
     "--model space-bunny-free --key anonymous", True),
    (t("LL Alpha Free（免费·Zen·256k·输出64k）"), "zen-ll-alpha", 3469,
     "--upstream http://127.0.0.1:8787/v1/chat/completions "
     "--model ll-alpha-free --key anonymous", True),
    (t("Nemotron 3.5 Super-VL（免费·Zen·1M）"), "zen-nemotron-vl", 3464,
     "--upstream http://127.0.0.1:8787/v1/chat/completions "
     "--model nemotron-3.5-super-vl-free --key anonymous", True),
    (t("LongCat 2.5 Preview（免费·Zen·1M）"), "zen-longcat", 3465,
     "--upstream http://127.0.0.1:8787/v1/chat/completions "
     "--model longcat-2.5-preview-free --key anonymous", True),
    (t("MiMo V2.6 Flash（免费·Zen·200k）"), "zen-mimo", 3467,
     "--upstream http://127.0.0.1:8787/v1/chat/completions "
     "--model mimo-v2.6-flash-free --key anonymous", True),
    (t("Big Pickle（免费·Zen·200k）"), "zen-big-pickle", 3468,
     "--upstream http://127.0.0.1:8787/v1/chat/completions "
     "--model big-pickle --key anonymous", True),
    (t("Kilo 文本 + 读图桥接（免费）"), "kilo-bridge", 3457,
     "--upstream https://api.kilo.ai/api/gateway/v1/chat/completions "
     "--model kilo-auto/free --key anonymous", False),
    (t("读图 Vision（免费·可看图）"), "vision", 3458,
     "--upstream https://api.kilo.ai/api/gateway/v1/chat/completions "
     "--model nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free --key anonymous", False),
    (t("Kilo 快稳 LFM2.5（免费·agent首选）"), "kilo-fast", 3461,
     "--upstream https://api.kilo.ai/api/gateway/v1/chat/completions "
     "--model liquid/lfm-2.5-2.6b:free --key anonymous", False),
    (t("DeepSeek-V4 (付费)"), "deepseek", None, "", False),
    (t("GLM-4-Flash（免费·稳定）"), "glm4", None, "", False),
    (t("GLM-4-Flash + 读图桥接（免费）"), "glm4-bridge", None, "", False),
]

# 打开本工具时自动连上的模型（= MODELS 里带 👑 的实测最优项）。
PREFERRED_PROFILE = "zen-space-bunny"

# 删除下拉框用的短标签（按钮显示名太长，Combobox 里放不下会被截断）
DEL_LABELS = {
    "zen-ds-flash": t("DS V4.1 Flash 免费·Zen"),
    "zen-space-bunny": t("👑 Space Bunny 免费·最快"),
    "zen-ll-alpha": t("LL Alpha 免费·256k"),
    "zen-nemotron-vl": t("Nemotron VL 免费·1M"),
    "zen-longcat": t("LongCat 免费·1M"),
    "zen-mimo": t("MiMo V2.6 免费"),
    "zen-big-pickle": t("Big Pickle 免费"),
    "deepseek":     t("DeepSeek-V4 付费"),
    "glm4":         t("GLM-4-Flash 免费"),
    "glm4-bridge":  t("GLM-4-Flash 免费+读图"),
    "glm41v":       t("GLM-4.1V 思考 免费"),
    "glm46v":       t("GLM-4.6V 免费"),
    "kilo-bridge":  t("Kilo 文本 免费+读图"),
    "kilo-fast":    t("Kilo LFM2.5 免费"),
    "vision":       t("读图 Vision 免费"),
}

PROFILES_DIR = os.path.join(HOME, ".claude", "profiles")
DELETE_DIR = os.path.join(HOME, ".claude", "backups", "deleted")


def port_open(port, timeout=0.4):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def http_ok(port, path, timeout=2.0):
    """比 TCP 探测更可靠：确认端口上确实是我们要的服务。"""
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        c.request("GET", path)
        r = c.getresponse()
        ok = r.status == 200
        r.read()
        c.close()
        return ok
    except Exception:
        return False


def wait_for(fn, timeout, interval=0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(interval)
    return False


def _spawn(args, logfile):
    """后台拉起进程。清掉代理环境变量——本机回环请求必须直连，
    否则会落到 http_proxy 上被拒。"""
    log = os.path.join(HOME, ".claude", logfile)
    env = os.environ.copy()
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
              "ALL_PROXY", "all_proxy"):
        env.pop(k, None)
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env["no_proxy"] = "127.0.0.1,localhost"
    with open(log, "ab") as lf:
        subprocess.Popen(args, stdout=lf, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, env=env,
                         creationflags=DETACHED, close_fds=True)


def ensure_bridge(timeout=90):
    """确保 zen_bridge 在 8787 可用；它会自己拉起 opencode serve(41234)。"""
    if http_ok(ZB_PORT, "/healthz", 1.5):
        return t("桥接已在运行"), True
    _spawn([CLAUDE_PY, ZB, "--port", str(ZB_PORT),
            "--sdk-port", str(ZB_SDK_PORT), "--auto-start"], "zen-bridge.log")
    if not wait_for(lambda: port_open(ZB_PORT), timeout):
        return t("桥接启动超时，见 ~/.claude/zen-bridge.log"), False
    if wait_for(lambda: http_ok(ZB_PORT, "/healthz", 3), 30):
        return t("桥接已启动"), True
    return t("桥接端口已开但健康检查未通过，见 ~/.claude/zen-bridge.log"), False


def ensure_proxy(port, args, timeout=30):
    if port is None:
        return t("（直连，无需代理）"), True
    if port_open(port):
        return t("代理已在运行"), True
    if args is None:
        # 外部工具装的模型：profile 在，但启动参数没登记，瞎起一个空 ccproxy
        # 反而更糟——如实告诉用户，settings 切换仍然生效。
        return (t("本地代理 %d 未运行，且该模型无登记启动参数，未自动拉起；"
                "请先手动启动对应代理") % port), False
    _spawn([CLAUDE_PY, CC, "--port", str(port)] + args.split(),
           "ccproxy-%d.log" % port)
    if wait_for(lambda: port_open(port), timeout):
        return t("已启动代理"), True
    return t("代理启动超时，见 ~/.claude/ccproxy-%d.log") % port, False


def current_model():
    try:
        s = json.load(open(os.path.join(HOME, ".claude", "settings.json"),
                           encoding="utf-8"))
        env = s.get("env", {})
        return env.get("ANTHROPIC_BASE_URL", ""), (s.get("model")
                                                   or env.get("ANTHROPIC_MODEL", ""))
    except Exception:
        return "", ""


def list_profile_names():
    """profiles 目录下所有 profile 名（不含 .json）。"""
    try:
        return sorted(f[:-5] for f in os.listdir(PROFILES_DIR)
                      if f.endswith(".json")
                      and os.path.isfile(os.path.join(PROFILES_DIR, f)))
    except OSError:
        return []


def delete_profile(name, permanent=False):
    """删除 profile。

    permanent=True（免费模型，profile 带 x_free）：os.remove 真删，不留 backup。
    permanent=False（付费模型）：移入 ~/.claude/backups/deleted/，可恢复。
    返回 (归档路径 or None, 是否真删)。
    """
    src = os.path.join(PROFILES_DIR, name + ".json")
    if not os.path.isfile(src):
        raise FileNotFoundError("profile 不存在: " + src)
    if permanent:
        os.remove(src)
        return None, True
    os.makedirs(DELETE_DIR, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(DELETE_DIR, "%s.json.deleted-%s" % (name, ts))
    shutil.move(src, dest)
    return dest, False


def profile_entry(name):
    """给 MODELS 里没登记、但 profiles 目录里真实存在的 profile 生成切换条目。

    外部工具（free-llm-installer、ccswitch.py）装的模型常有这种「有 profile、
    没登记」的情况。启动参数填 None=不知道，切换时不瞎拉起代理。
    """
    try:
        p = json.load(open(os.path.join(PROFILES_DIR, name + ".json"),
                           encoding="utf-8"))
    except Exception:
        return None
    env = p.get("env") or {}
    base = env.get("ANTHROPIC_BASE_URL", "") or ""
    port = None
    m = re.search(r"127\.0\.0\.1:(\d+)", base)
    if m:
        port = int(m.group(1))
    mid = p.get("model") or env.get("ANTHROPIC_MODEL", "") or ""
    mid = mid.split("/")[-1].split("[")[0].strip()
    label = DEL_LABELS.get(name) or name
    disp = "%s（%s）" % (label, mid[:30]) if mid else label
    bridge = "vision-bridge" in (p.get("mcpServers") or {})
    # 启动参数优先读 profile 自带的 x_ccproxy_args —— 用界面新建的模型就是
    # 靠它在切换时自动拉起代理。读不到才返回 None（外部工具装的，参数未知，
    # 这时 ensure_proxy 会如实说「没登记参数，没自动拉起」而不是瞎起一个）。
    cc = p.get("x_ccproxy_args")
    args = " ".join(str(a) for a in cc) if cc else None
    return (disp, name, port, args, bridge)


def switch(profile, port, args, bridge):
    pf = os.path.join(HOME, ".claude", "profiles", profile + ".json")
    prof = json.load(open(pf, encoding="utf-8"))
    sp = os.path.join(HOME, ".claude", "settings.json")
    settings = json.load(open(sp, encoding="utf-8"))
    settings["env"] = prof.get("env", {})
    settings["model"] = prof.get("model")
    if "modelSettings" in prof:
        settings["modelSettings"] = prof["modelSettings"]
    # 读图桥接：profile 带 mcpServers 就注入，否则清掉（避免残留 MCP）
    if "mcpServers" in prof:
        settings["mcpServers"] = prof["mcpServers"]
    else:
        settings.pop("mcpServers", None)
    tmp = sp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
    os.replace(tmp, sp)

    notes = []
    ok = True
    if bridge:
        msg, ok = ensure_bridge()
        notes.append(msg)
        if ok:
            pmsg, pok = ensure_proxy(port, args)
            notes.append(pmsg)
            ok = ok and pok
    else:
        pmsg, pok = ensure_proxy(port, args)
        notes.append(pmsg)
        ok = pok
    return "; ".join(notes), ok


class ClaudeFrame(tk.Frame):
    """Claude Code 的模型切换页。"""

    def __init__(self, master):
        super().__init__(master)
        self._task = None
        self._del_map = {}
        self.models = []
        self.buttons = []
        self.missing = []
        self._auto_after = None

        tk.Label(self, text=t("点哪个模型，就切到哪个。切完重启 Claude Code 即可"),
                 font=("Microsoft YaHei", 11), pady=6).pack()

        self.status = tk.Label(self, text="", fg="green", font=("Microsoft YaHei", 9),
                               wraplength=800, justify="left", height=3)
        self.status.pack(pady=4)

        # ---- 模型按钮区：可滚动 ----
        body = tk.Frame(self)
        body.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(body, highlightthickness=0, bd=0,
                                yscrollincrement=22, bg=self.cget("bg"))
        self.vsb = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vsb.pack(side="right", fill="y")

        self.list_frame = tk.Frame(self.canvas)
        self._canvas_win = self.canvas.create_window((0, 0), window=self.list_frame,
                                                     anchor="nw")
        self.list_frame.bind("<Configure>", self._on_list_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        # MODELS 里有登记、但 profile 已不存在的条目提示
        self.missing_label = tk.Label(self.list_frame, text="", fg="gray",
                                      font=("Microsoft YaHei", 9),
                                      wraplength=760, justify="left")

        self.rebuild_models()
        self.rebuild_buttons()

        # 滚轮（Windows/delta 与 X11/Button-4,5 两套事件都接），PgUp/PgDn 翻页
        self.bind_all("<MouseWheel>", self._on_mousewheel, add="+")
        self.bind_all("<Button-4>", self._on_mousewheel, add="+")
        self.bind_all("<Button-5>", self._on_mousewheel, add="+")
        self.bind("<Prior>", lambda e: self._scroll_page(-1))
        self.bind("<Next>", lambda e: self._scroll_page(1))

        # ---- 删除模型 ----
        sep = tk.Frame(self, height=2, bd=1, relief="sunken")
        sep.pack(fill="x", padx=26, pady=8)
        tk.Label(self, text=t("删除模型（免费模型真删·付费归档可恢复）"),
                 font=("Microsoft YaHei", 9), fg="gray").pack()
        self.del_combo = ttk.Combobox(self, state="readonly", height=12,
                                      font=("Microsoft YaHei", 10))
        self.del_combo.pack(fill="x", padx=26, pady=4)
        btns = tk.Frame(self)
        btns.pack(fill="x", padx=26, pady=2)
        tk.Button(btns, text=t("刷新列表"), width=10,
                  command=self.refresh).pack(side="left")
        tk.Button(btns, text=t("＋ 新建模型"), width=12,
                  command=self.on_new).pack(side="left", padx=6)
        tk.Button(btns, text=t("删除所选模型"), width=14, fg="darkred",
                  command=self.on_delete).pack(side="right")

        self.refresh()
        # 打开即自动连上皇冠模型（静默，不弹框）
        self._auto_after = self.after(400, self._auto_connect)

    def cleanup(self):
        """主窗口关闭前调用：取消待执行的定时器，避免 invalid command name。"""
        if self._auto_after is not None:
            try:
                self.after_cancel(self._auto_after)
            except Exception:
                pass
            self._auto_after = None

    # ---------- 滚动区 ----------
    def _on_list_configure(self, _e=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._update_scrollbar()

    def _on_canvas_configure(self, e):
        self.canvas.itemconfigure(self._canvas_win, width=e.width)
        self._update_scrollbar()

    def _update_scrollbar(self):
        try:
            need = self.list_frame.winfo_reqheight() > self.canvas.winfo_height()
        except Exception:
            return
        if need and not self.vsb.winfo_ismapped():
            self.vsb.pack(side="right", fill="y")
        elif not need and self.vsb.winfo_ismapped():
            self.vsb.pack_forget()
            self.canvas.yview_moveto(0)

    def _wheel_here(self):
        """指针是否落在本页的模型列表上（排除删除下拉框，免得抢它自己的滚动）。"""
        try:
            w = self.winfo_containing(*self.winfo_pointerxy())
        except Exception:
            return False
        while w is not None:
            if w is self.canvas:
                return True
            if w is getattr(self, "del_combo", None):
                return False
            w = getattr(w, "master", None)
        return False

    def _on_mousewheel(self, event):
        if not self.vsb.winfo_ismapped() or not self._wheel_here():
            return
        num = getattr(event, "num", None)
        if num == 4:
            step = -1
        elif num == 5:
            step = 1
        else:
            step = -1 if getattr(event, "delta", 0) > 0 else 1
        self.canvas.yview_scroll(step, "units")

    def _scroll_page(self, direction):
        if self.vsb.winfo_ismapped():
            self.canvas.yview_scroll(direction, "pages")

    # ---------- 动态模型列表 ----------
    def rebuild_models(self):
        """重新拉取「现在到底有哪些模型」，让可切换与可删除永远是同一批。"""
        installed = set(list_profile_names())
        entries = []
        missing = []
        for disp, prof, port, args, bridge in MODELS:
            if prof in installed:
                entries.append((disp, prof, port, args, bridge))
            else:
                missing.append(disp)
        used = {e[1] for e in entries}
        for name in sorted(installed - used):
            e = profile_entry(name)
            if e:
                entries.append(e)
        self.models = entries
        self.missing = missing

    def rebuild_buttons(self):
        """按 self.models 重画按钮（删除/安装模型后调用，两个列表同步变）。"""
        # 先解除提示标签的布局，否则重排的按钮会插到它上面
        self.missing_label.pack_forget()
        for b in self.buttons:
            b.destroy()
        self.buttons = []
        for disp, prof, port, args, bridge in self.models:
            b = tk.Button(self.list_frame, text=disp, height=2,
                          font=("Microsoft YaHei", 11),
                          command=lambda d=disp, p=prof, pt=port, a=args, br=bridge:
                          self.on_click(d, p, pt, a, br))
            b.pack(fill="x", padx=26, pady=5)
            self.buttons.append(b)
        if self.missing:
            self.missing_label.config(
                text=t("未安装、已从列表隐藏：%s") % "、".join(self.missing))
            self.missing_label.pack(fill="x", padx=26, pady=(4, 0))
        else:
            self.missing_label.pack_forget()
        self.update_idletasks()

    def _auto_connect(self):
        """启动时静默切到 PREFERRED_PROFILE，把桥接和网关一并拉起。"""
        self._auto_after = None
        for disp, prof, port, args, bridge in MODELS:
            if prof != PREFERRED_PROFILE:
                continue
            if not os.path.isfile(os.path.join(PROFILES_DIR, prof + ".json")):
                return
            self.on_click(disp, prof, port, args, bridge, quiet=True)
            return

    def refresh_del_list(self):
        """下拉列表 = self.models 的 profile 集合（与切换按钮严格一致）。"""
        self._del_map = {}
        labels = []
        for _disp, prof, _port, _args, _bridge in self.models:
            label = DEL_LABELS.get(prof)
            if not label:
                try:
                    p = json.load(open(os.path.join(PROFILES_DIR, prof + ".json"),
                                       encoding="utf-8"))
                    mid = p.get("model") or (p.get("env") or {}).get("ANTHROPIC_MODEL", "")
                    mid = mid.split("/")[-1].split("[")[0].strip()
                    label = ("%s（%s）" % (prof, mid[:28])) if mid else prof
                except Exception:
                    label = prof
            text = "%s  [%s]" % (label, prof)
            labels.append(text)
            self._del_map[text] = prof
        self.del_combo["values"] = labels
        if labels and not self.del_combo.get():
            self.del_combo.current(0)
        elif self.del_combo.get() not in self._del_map:
            self.del_combo.set("")

    # ---------- 新建模型（不用改代码）----------
    def next_port(self):
        """下一个没被占用的本地代理端口（现有 profile 用过的都跳过）。"""
        used = set()
        if os.path.isdir(PROFILES_DIR):
            for fn in os.listdir(PROFILES_DIR):
                if not fn.endswith(".json"):
                    continue
                p = read_json(os.path.join(PROFILES_DIR, fn))
                base = str(((p or {}).get("env") or {})
                           .get("ANTHROPIC_BASE_URL") or "")
                m = re.search(r"127\.0\.0\.1:(\d+)", base)
                if m:
                    used.add(int(m.group(1)))
        port = 3470
        while port in used:
            port += 1
        return port

    def create_profile(self, mid, port):
        """按模型 ID 生成 profile，返回 (name, args)；失败返回 (None, 原因)。

        链路判断只看 ID 形状：带斜杠的是网关模型（Kilo 直连），不带斜杠的是
        Zen 免费模型（必须经本地桥接 8787，直连会被上游 403）。
        """
        via_bridge = "/" not in mid
        upstream = ("http://127.0.0.1:%d/v1/chat/completions" % ZB_PORT) \
            if via_bridge else KILO_UPSTREAM
        args = "--upstream %s --model %s --key anonymous" % (upstream, mid)

        name = re.sub(r"[^0-9A-Za-z]+", "-", mid).strip("-").lower()
        if not name:
            return None, t("模型 ID 里没有可用字符")
        dst = os.path.join(PROFILES_DIR, name + ".json")
        if os.path.isfile(dst):
            return None, t("已经有同名 profile：%s.json") % name

        prof = {
            "$schema": "https://json.schemastore.org/claude-code-settings.json",
            "x_note": t("由模型管理器新建（%s）")
                      % (t("Zen 桥接") if via_bridge else t("Kilo 直连")),
            "x_gateway_port": port,
            # 写进 profile，以后从列表切它时才能自动把代理拉起来
            "x_ccproxy_args": args.split(),
            "env": {
                "ANTHROPIC_AUTH_TOKEN": _auth_token(),
                "ANTHROPIC_BASE_URL": "http://127.0.0.1:%d" % port,
                "ANTHROPIC_MODEL": mid + "[1m]",
                "ANTHROPIC_DEFAULT_OPUS_MODEL": mid + "[1m]",
                "ANTHROPIC_DEFAULT_OPUS_MODEL_NAME": mid,
                "ANTHROPIC_DEFAULT_SONNET_MODEL": mid + "[1m]",
                "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME": mid,
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": mid + "[1m]",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME": mid,
                "CLAUDE_CODE_SUBAGENT_MODEL": mid + "[1m]",
                "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "128000" if via_bridge else "56000",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "API_TIMEOUT_MS": "600000",
                "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "16000",
            },
            "model": mid + "[1m]",
            "tui": "fullscreen",
            "modelSettings": {mid: {"effortLevel": "medium"}},
            "x_free": True,       # 新建的都是免费模型：删的时候真删，不留备份
        }
        if via_bridge:
            prof["x_bridge"] = True
        os.makedirs(PROFILES_DIR, exist_ok=True)
        tmp = dst + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(prof, f, ensure_ascii=False, indent=2)
        os.replace(tmp, dst)
        return name, args

    def on_new(self):
        dlg = tk.Toplevel(self)
        dlg.title(t("新建模型（Claude Code）"))
        dlg.transient(self)
        dlg.geometry("780x620")
        dlg.resizable(True, True)
        dlg.configure(bg="white")
        f = {"font": ("Microsoft YaHei", 10)}

        # ---- 从目录里挑：列表带性能数据，代替原来那截显示不全的下拉 ----
        top = tk.Frame(dlg, bg="white")
        top.pack(fill="x", padx=18, pady=(14, 2))
        tk.Label(top, text=t("从可用免费模型里选（点一行自动填到下面）"),
                 bg="white", font=("Microsoft YaHei", 10)).pack(side="left")
        lbl_scan = tk.Label(top, text="", bg="white", fg="gray",
                            font=("Microsoft YaHei", 8))
        lbl_scan.pack(side="right")

        bar = tk.Frame(dlg, bg="white")
        bar.pack(fill="x", padx=18, pady=(0, 4))
        b_scan = tk.Button(bar, text=t("刷新目录"), width=10,
                           font=("Microsoft YaHei", 9))
        b_bench = tk.Button(bar, text=t("测速排序"), width=10,
                            font=("Microsoft YaHei", 9),
                            bg=ACCENT, fg="white", relief="flat",
                            disabledforeground="#edf2fe")
        b_scan.pack(side="left")
        b_bench.pack(side="left", padx=8)
        tk.Label(bar, text=t("「测速排序」逐个量首字耗时（约半分钟），灰=未测 红=挂"),
                 bg="white", fg="gray", font=("Microsoft YaHei", 8)).pack(
            side="left", padx=4)

        cands = []          # [(id, 来源, 上下文k)]
        bench = load_bench_cache()   # {id: {"ttft": 秒或None, "ts": ..}}
        installed = {m.get("id") for m in (read_json(MODELS_JSON) or [])}

        cols = ("mid", "src", "ctx", "inst", "ttft")
        wrap = tk.Frame(dlg, bg="white")
        wrap.pack(fill="both", expand=True, padx=18, pady=(6, 0))
        tree = ttk.Treeview(wrap, columns=cols, show="headings", height=10)
        for cid, txt, w, anch in (
                ("mid", t("模型 ID"), 330, "w"),
                ("src", t("来源"), 52, "center"),
                ("ctx", t("上下文"), 64, "e"),
                ("inst", t("已装"), 48, "center"),
                ("ttft", t("首字实测"), 78, "e")):
            tree.heading(cid, text=txt)
            tree.column(cid, width=w, anchor=anch)
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        tree.tag_configure("ok", foreground="#1a7f37")
        tree.tag_configure("fail", foreground="#c0392b")
        tree.tag_configure("dim", foreground="#9aa1a9")

        def render():
            """按「实测快的在前 → 没测过 → 挂了」排，行里标已装/首字。"""
            def key(c):
                b = bench.get(c[0])
                if b is None:
                    return (1, 0.0)
                return (0, b["ttft"]) if b["ttft"] is not None else (2, 0.0)
            tree.delete(*tree.get_children())
            for mid, src, ctx in sorted(cands, key=key):
                b = bench.get(mid)
                if b is None:
                    ttft, tag = "—", "dim"
                elif b["ttft"] is None:
                    ttft, tag = t("超时/挂"), "fail"
                else:
                    ttft, tag = "%.2fs" % b["ttft"], "ok"
                tree.insert("", "end", iid=mid, tags=(tag,), values=(
                    mid, src, ("%dk" % ctx) if ctx else "—",
                    "✓" if mid in installed else "", ttft))

        def fill_row(*_a):
            sel = tree.selection()
            if sel:
                e_id.delete(0, "end")
                e_id.insert(0, sel[0])
                upd()
        tree.bind("<<TreeviewSelect>>", fill_row)

        def set_btns(scanning=False, benching=False, n=0):
            b_scan.config(state="disabled" if (scanning or benching) else "normal",
                          cursor="arrow" if (scanning or benching) else "hand2")
            b_bench.config(state="disabled" if (scanning or benching) else "normal",
                           cursor="arrow" if (scanning or benching) else "hand2",
                           text=(t("测速中 %d/%d", n, len(cands)) if benching
                                 else t("测速排序")))
            b_scan.config(text=t("扫描中") if scanning else t("刷新目录"))

        def scan():
            set_btns(scanning=True)
            lbl_scan.config(text=t("正在拉 Kilo / Zen 目录…"))

            def work():
                got = scan_candidates()

                def done():
                    set_btns()
                    cands[:] = got
                    render()
                    if got:
                        lbl_scan.config(text=t("共 %d 个", len(got)))
                    else:
                        lbl_scan.config(text=t("没扫到（网络问题？可手填）"))
                dlg.after(0, done)
            threading.Thread(target=work, daemon=True).start()
        b_scan.config(command=scan)

        def do_bench():
            if not cands:
                lbl_scan.config(text=t("目录还是空的，先「刷新目录」"))
                return
            set_btns(benching=True, n=0)

            def prog(i, total, mid, ttft):
                def step():
                    b_bench.config(text=t("测速中 %d/%d", i, total))
                dlg.after(0, step)

            def work():
                nonlocal bench
                bench = bench_candidates(cands, progress=prog)

                def done():
                    set_btns()
                    render()
                    ok = sum(1 for c in cands
                             if (bench.get(c[0]) or {}).get("ttft"))
                    lbl_scan.config(text=t("测速完成 %d/%d 有响应，已按快慢排")
                                    % (ok, len(cands)))
                dlg.after(0, done)
            threading.Thread(target=work, daemon=True).start()
        b_bench.config(command=do_bench)

        tk.Label(dlg, text=t("模型 ID（也可以直接手写）"), bg="white",
                 anchor="w").pack(fill="x", padx=18, pady=(10, 2))
        e_id = tk.Entry(dlg, **f)
        e_id.pack(fill="x", padx=18)

        hint = tk.Label(dlg, text="", bg="white", fg=ACCENT,
                        font=("Microsoft YaHei", 9), anchor="w")
        hint.pack(fill="x", padx=18, pady=(0, 8))

        row = tk.Frame(dlg, bg="white")
        row.pack(fill="x", padx=18, pady=2)
        tk.Label(row, text=t("本地代理端口"), bg="white",
                 font=("Microsoft YaHei", 10)).pack(side="left")
        e_port = tk.Entry(row, width=8, **f)
        e_port.insert(0, str(self.next_port()))
        e_port.pack(side="left", padx=8)
        tk.Label(row, text=t("（自动挑一个没占用的）"), bg="white", fg="gray",
                 font=("Microsoft YaHei", 8)).pack(side="left")

        def upd(*_a):
            mid = e_id.get().strip()
            if not mid:
                hint.config(text="")
            elif "/" in mid:
                hint.config(text=t("→ 走 Kilo 网关直连（ID 带斜杠）"))
            else:
                hint.config(text=t("→ 走 Zen 免费，经本地桥接 8787"))
        e_id.bind("<KeyRelease>", upd)
        upd()

        def do_create():
            mid = e_id.get().strip()
            if not mid:
                messagebox.showwarning(t("缺模型 ID"), t("先填模型 ID"), parent=dlg)
                return
            try:
                port = int(e_port.get().strip())
            except ValueError:
                messagebox.showwarning(t("端口不对"), t("端口要填数字"), parent=dlg)
                return
            name, args = self.create_profile(mid, port)
            if name is None:
                messagebox.showerror(t("创建失败"), args, parent=dlg)
                return
            dlg.destroy()
            self.refresh()
            # 创建完直接切过去，顺带把代理（Zen 的还有桥接）拉起来
            self.on_click(mid, name, port, args, "/" not in mid)

        bar = tk.Frame(dlg, bg="white")
        bar.pack(fill="x", padx=18, pady=16)
        tk.Button(bar, text=t("取消"), width=10, command=dlg.destroy).pack(side="right")
        tk.Button(bar, text=t("创建并切换"), width=14, bg=ACCENT, fg="white",
                  relief="flat", font=("Microsoft YaHei", 10),
                  command=do_create).pack(side="right", padx=8)
        e_id.focus_set()
        dlg.grab_set()
        scan()      # 打开就自动拉目录，不用再手点「刷新目录」

    def on_delete(self):
        text = self.del_combo.get().strip()

        name = getattr(self, "_del_map", {}).get(text, text)
        if not name:
            messagebox.showwarning(t("没有选中"), t("先在下拉框里选一个要删除的模型"))
            return
        try:
            prof = json.load(open(os.path.join(PROFILES_DIR, name + ".json"),
                                  encoding="utf-8"))
        except Exception as e:
            messagebox.showerror(t("读不了这个 profile"), str(e))
            return
        model = prof.get("model") or (prof.get("env") or {}).get("ANTHROPIC_MODEL", "?")
        is_free = bool(prof.get("x_free"))
        if is_free:
            body = t("将【永久删除】免费模型「%s」\nprofile 文件: %s\n上游模型 ID: %s\n\n"
                    "免费模型直接真删，不留 backup。\n确定删除？") \
                    % (text, name + ".json", model)
        else:
            body = t("将删除模型「%s」\nprofile 文件: %s\n上游模型 ID: %s\n\n"
                    "操作 = 把该 profile 移入\n~/.claude/backups/deleted/\n"
                    "（不是硬删，需要时可捡回）\n\n确定删除？") \
                    % (text, name + ".json", model)
        if not messagebox.askyesno(t("确认删除"), body):
            return
        try:
            dest, real_delete = delete_profile(name, permanent=is_free)
        except Exception as e:
            messagebox.showerror(t("删除失败"), str(e))
            return
        # 如果删的是当前生效的 profile，提醒一句（settings.json 本身不动）
        cur_base, cur_model = current_model()
        pbase = (prof.get("env") or {}).get("ANTHROPIC_BASE_URL", "")
        pmodel = prof.get("model") or (prof.get("env") or {}).get("ANTHROPIC_MODEL", "")
        extra = ""
        if pbase == cur_base and pmodel == cur_model:
            extra = t("\n\n注意：这是当前正在用的模型。settings.json 未改动，"
                     "但已无 profile 与它对应，建议再点一个别的模型。")
        self.refresh_del_list()
        self.refresh()
        if real_delete:
            done_msg = t("「%s」已永久删除（免费模型，无 backup）。") % text
        else:
            done_msg = t("「%s」已移除。\n归档到：%s（可恢复）") % (text, dest)
        messagebox.showinfo(
            t("已删除"),
            t("%s%s\n\n重启 Claude Code 后，切换器列表不再显示它。")
            % (done_msg, extra))

    def refresh(self):
        # 磁盘上的 profile 可能刚被外部工具增删过，先重拉列表再匹配当前模型
        self.rebuild_models()
        self.rebuild_buttons()
        cur_base, cur_model = current_model()
        try:
            s = json.load(open(os.path.join(HOME, ".claude", "settings.json"),
                               encoding="utf-8"))
            has_bridge = "vision-bridge" in s.get("mcpServers", {})
        except Exception:
            has_bridge = False
        cur_disp = t("未知")
        for disp, prof, port, args, bridge in self.models:
            try:
                p = json.load(open(os.path.join(HOME, ".claude", "profiles",
                                                prof + ".json"), encoding="utf-8"))
            except Exception:
                continue
            pbase = p["env"]["ANTHROPIC_BASE_URL"]
            pmodel = p.get("model") or p["env"].get("ANTHROPIC_MODEL", "")
            pbridge = "vision-bridge" in p.get("mcpServers", {})
            if pbase == cur_base and pmodel == cur_model and pbridge == has_bridge:
                cur_disp = disp
                break
        # 当前在用的那个按钮标绿：8 个按钮长得一样，光靠状态栏文字要来回对
        for i, (disp, _p, _po, _a, _b) in enumerate(self.models):
            if i >= len(self.buttons):
                break
            self.buttons[i].config(
                fg="#1a7f37" if disp == cur_disp else "black",
                relief="groove" if disp == cur_disp else "raised",
                bd=2 if disp == cur_disp else 1)
        self.status.config(
            text=t("当前：%s\n切换后重启 Claude Code；想保留上下文重启时加 --continue")
                 % cur_disp, fg="blue")
        self.refresh_del_list()

    def _set_busy(self, busy, text=None, color=None):
        state = tk.DISABLED if busy else tk.NORMAL
        for b in self.buttons:
            b.config(state=state)
        if text is not None:
            self.status.config(text=text, fg=color or "blue")
        self.update_idletasks()

    def on_click(self, disp, prof, port, args, bridge, quiet=False):
        if not os.path.isfile(os.path.join(PROFILES_DIR, prof + ".json")):
            if quiet:
                return
            messagebox.showwarning(
                t("该模型已删除"),
                t("「%s」的 profile 已被删除。\n点下方「刷新列表」或重启本工具。")
                % disp)
            self.refresh_del_list()
            return
        self._task = {"disp": disp, "prof": prof, "port": port, "args": args,
                      "bridge": bridge, "quiet": quiet, "done": False,
                      "result": None}
        tip = (t("正在拉起 Zen 桥接（约 30 秒，含 opencode serve 冷启动）...")
               if bridge else t("正在切换..."))
        self._set_busy(True, tip, "#b8860b")
        threading.Thread(target=self._worker, daemon=True).start()
        self.after(300, self._poll)

    def _worker(self):
        t = self._task
        try:
            t["result"] = switch(t["prof"], t["port"], t["args"], t["bridge"])
        except Exception as e:
            t["result"] = (t("失败：%s") % e, False)
        finally:
            t["done"] = True

    def _poll(self):
        t = self._task
        if t is None:
            return
        if not t["done"]:
            self.after(300, self._poll)
            return
        msg, ok = t["result"]
        self._set_busy(False)
        if t.get("quiet"):
            # 自动连接：只在状态栏显示，不弹框（每次开工具都弹一次很烦）
            self._set_busy(False, (t("已自动连上 %s（%s）") % (t["disp"], msg)) if ok
                           else (t("自动连接失败：%s") % msg),
                           "green" if ok else "darkred")
            self._task = None
            self.refresh()
            return
        # 取该 profile 真实的模型 ID
        try:
            pf = json.load(open(os.path.join(PROFILES_DIR, t["prof"] + ".json"),
                                encoding="utf-8"))
            mid = pf.get("model") or (pf.get("env") or {}).get("ANTHROPIC_MODEL", "")
        except Exception:
            mid = ""
        extra = ""
        if t["bridge"] and ok and mid:
            extra = (t("\n\n桥接已就绪，WorkBuddy 侧也能用同一个模型：\n"
                     "模型列表选 vendor=opencode 的 %s") % mid)
        messagebox.showinfo(
            t("已切换") if ok else t("部分失败"),
            t("已切换到：%s\n%s%s\n\n重启 Claude Code 后生效。\n"
            "想接着之前的对话继续：重启时用 claude --continue\n"
            "（换模型不会丢会话，历史都在 ~/.claude/projects/）")
            % (t["disp"], msg, extra))
        self._task = None
        self.refresh()


# ===========================================================================
# 主窗口
# ===========================================================================
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(t("模型管理器") + " v" + APP_VERSION)
        scr_w = self.winfo_screenwidth()
        scr_h = self.winfo_screenheight()
        w = min(900, max(760, scr_w - 200))
        # 别顶到任务栏：留 160px 给标题栏 + 任务栏 + 一点边
        h = min(880, max(640, scr_h - 160))
        self.geometry("%dx%d" % (w, h))
        self.minsize(720, 600)
        self.configure(bg=BG)

        # 菜单栏：帮助 -> 关于
        menubar = tk.Menu(self)
        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label=t("关于"), command=self._show_about)
        menubar.add_cascade(label=t("帮助"), menu=help_menu)
        self.configure(menu=menubar)

        # 语言选择：自动检测系统 UI 语言；手动改后写盘记住，并重启生效
        lang_bar = tk.Frame(self, bg=BG)
        lang_bar.pack(fill="x", padx=8, pady=(4, 2))
        tk.Label(lang_bar, text=t("语言"), bg=BG, fg=SUB,
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.lang_combo = ttk.Combobox(lang_bar, state="readonly", width=12,
                                       font=("Microsoft YaHei UI", 9))
        self.lang_combo["values"] = [LANG_NAMES[c] for c in LANG_ORDER]
        try:
            self.lang_combo.current(LANG_ORDER.index(_current_lang))
        except Exception:
            self.lang_combo.current(0)
        self.lang_combo.pack(side="left", padx=4)
        self.lang_combo.bind("<<ComboboxSelected>>", self._on_lang)

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", font=("Microsoft YaHei UI", 10),
                        padding=(18, 8))
        style.map("TNotebook.Tab",
                  background=[("selected", BG), ("!selected", "#eef0f3")],
                  foreground=[("selected", ACCENT), ("!selected", SUB)])

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=2, pady=(6, 2))

        self.free_llm = FreeLLMFrame(nb)
        self.claude = ClaudeFrame(nb)
        nb.add(self.free_llm, text=t("WorkBuddy 免费模型"))
        nb.add(self.claude, text=t("Claude Code 模型"))
        self.notebook = nb
        # 显式选中第一页：Notebook 的初始选中项受 add 顺序和焦点影响，
        # 实测偶发会停在第二页，这里钉死，总从「免费模型」开始看。
        nb.select(0)

        # 切页时刷新一下（另一页的操作可能改了磁盘上的东西）
        nb.bind("<<NotebookTabChanged>>", self._on_tab)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_tab(self, _e=None):
        try:
            cur = self.notebook.select()
            if str(cur) == str(self.claude):
                self.claude.refresh()
            elif str(cur) == str(self.free_llm):
                self.free_llm.refresh_table()
        except Exception:
            pass

    def _on_lang(self, _e=None):
        """手动切换语言：写盘记住，并重启本进程让所有文案立即生效。"""
        name = self.lang_combo.get()
        code = next((c for c in LANG_ORDER if LANG_NAMES[c] == name), None)
        if code and code != _current_lang:
            save_lang(code)
            self.destroy()
            os.execv(sys.executable, [sys.executable] + sys.argv)

    def _on_close(self):
        try:
            self.claude.cleanup()
        except Exception:
            pass
        self.destroy()

    def _show_about(self):
        """关于对话框：显示应用名、版本、简介与源码地址。"""
        dlg = tk.Toplevel(self)
        dlg.title(t("关于"))
        dlg.configure(bg=BG)
        dlg.resizable(False, False)
        try:
            dlg.transient(self)
            dlg.grab_set()
        except Exception:
            pass
        pad = dict(padx=18, pady=4)

        tk.Label(dlg, text=t("模型管理器") + "  v" + APP_VERSION,
                 font=("Microsoft YaHei UI", 14, "bold"), bg=BG, fg=FG).pack(pady=(16, 4))
        tk.Label(dlg, text=t("合并管理 WorkBuddy 免费模型与 Claude Code 模型的小工具。"),
                 font=("Microsoft YaHei UI", 10), bg=BG, fg=SUB, wraplength=360,
                 justify="left").pack(padx=18, pady=(2, 10))

        info = tk.Frame(dlg, bg=BG)
        info.pack(fill="x", padx=18)
        tk.Label(info, text=t("本机版本："), bg=BG, fg=SUB,
                 font=("Microsoft YaHei UI", 10), anchor="w").grid(row=0, column=0, sticky="w", pady=2)
        tk.Label(info, text=APP_VERSION, bg=BG, fg=FG,
                 font=("Microsoft YaHei UI", 10), anchor="w").grid(row=0, column=1, sticky="w", pady=2, padx=(6, 0))
        tk.Label(info, text=t("源码与更新："), bg=BG, fg=SUB,
                 font=("Microsoft YaHei UI", 10), anchor="w").grid(row=1, column=0, sticky="w", pady=2)
        tk.Label(info, text="github.com/oracis/model-manager",
                 bg=BG, fg=ACCENT, font=("Microsoft YaHei UI", 10), anchor="w").grid(row=1, column=1, sticky="w", pady=2, padx=(6, 0))

        tk.Button(dlg, text=t("关闭"), command=dlg.destroy,
                  font=("Microsoft YaHei UI", 10), relief="flat", cursor="hand2",
                  bg="#eef0f3", fg=FG, padx=18, pady=6).pack(pady=(12, 16))
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        dlg.update_idletasks()
        dlg.geometry("+%d+%d" % ((self.winfo_rootx() + max(0, (self.winfo_width() - dlg.winfo_width()) // 2)),
                                (self.winfo_rooty() + max(0, (self.winfo_height() - dlg.winfo_height()) // 2))))


if __name__ == "__main__":
    try:
        App().mainloop()
    except Exception:
        import traceback
        log = os.path.join(HOME, ".claude", "model-manager-error.log")
        with open(log, "a", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        try:
            messagebox.showerror(t("模型管理器出错"), "详情见 " + log)
        except Exception:
            pass
