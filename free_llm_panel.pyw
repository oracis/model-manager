#!/usr/bin/env pythonw
# -*- coding: utf-8 -*-
"""free_llm_panel.pyw —— 免费模型面板：看当前配置、一键体检/重选。

⚠️ 2026-10-19 起日常入口已合并到 `model_manager.pyw` 的第一个 Tab（桌面「模型管理器.cmd」）。
本文件保留可单独运行，但两处逻辑是同一份，改动记得同步。

为什么要有这个：免费池随时限流、下线、变慢，等发现「怎么这么卡」时
往往已经跑了几轮。这里给四个按钮：

    重新择优      全量测速选前 5（约 1~2 分钟，会改配置）
    先看一眼      同样的比较，但一个字节都不写
    快速体检      只测当前几个，坏了才改配置（约 10 秒）
    撤销改动      恢复到上一次改动之前的备份

改完 models.json 后 WorkBuddy 要重启才会读到新列表，界面上会提示。

启动：C:\\Python313\\pythonw.exe free_llm_panel.pyw
（用系统 Python，因为受管 Python 的 3.13.12 没带 tkinter）
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

HOME = os.path.expanduser("~")
MODELS = os.path.join(HOME, ".workbuddy", "models.json")
STATE = os.path.join(HOME, ".workbuddy", "free_llm_auto_state.json")
SCRIPT = os.path.join(HOME, ".workbuddy", "skills", "free-llm-installer",
                      "scripts", "install_free_llms.py")
CLAUDE_SETTINGS = os.path.join(HOME, ".claude", "settings.json")
BACKUP_DIR = os.path.join(HOME, ".workbuddy", "backups")
MAX_BACKUPS = 3          # models.json 备份只留最近几份
BRIDGE_URL = "http://127.0.0.1:8787/v1/chat/completions"
PY = sys.executable
NO_WINDOW = 0x08000000

BG = "#ffffff"
FG = "#1f2328"
SUB = "#6a737d"
ACCENT = "#2f6fed"


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


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


def fmt_ts(ts):
    if not ts:
        return "从未"
    return time.strftime("%m-%d %H:%M", time.localtime(ts))


def claude_hint():
    """看 Claude Code 现在用的是哪个模型、是否经过共享的 8787 桥接。

    面板只改 ~/.workbuddy/models.json，和 Claude Code 的配置是两套，
    但两边可能共用同一个本地桥接进程，所以这里要把「会互相挤」的情况说清楚。

    返回 (text, via_bridge)；没装/没配置时 text 为空串。
    """
    s = read_json(CLAUDE_SETTINGS)
    if not isinstance(s, dict):
        return "", False
    env = s.get("env") or {}
    base = str(env.get("ANTHROPIC_BASE_URL") or "")
    model = str(s.get("model") or env.get("ANTHROPIC_MODEL") or "")

    local = ("127.0.0.1" in base) or ("localhost" in base)
    if not local:
        return "Claude Code：%s（直连，不受影响）" % (model or "官方"), False

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
        return "Claude Code：%s（经同一桥接，测速时会排队）" % model, True
    return "Claude Code：%s（本地代理，不共用桥接）" % model, False


class Panel(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("免费模型面板")
        self.geometry("780x600")
        self.minsize(700, 480)
        self.configure(bg=BG)
        self.running = False
        self.claude_via_bridge = False
        self.mode = ""

        pad = {"padx": 14}

        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", pady=(14, 6), **pad)
        tk.Label(head, text="免费模型", font=("Microsoft YaHei UI", 15, "bold"),
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
        for col, txt, w, anchor in (("n", "#", 36, "center"),
                                    ("id", "模型 ID", 470, "w"),
                                    ("vendor", "来源", 120, "w"),
                                    ("mode", "方式", 70, "center")):
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

        self.btn_pick = mkbtn("重新择优", lambda: self.run("repick"), True)
        self.btn_preview = mkbtn("先看一眼", lambda: self.run("preview"))
        self.btn_check = mkbtn("快速体检", lambda: self.run("check"))
        self.btn_undo = mkbtn("撤销改动", self.undo)
        self.status = tk.Label(bar, text="就绪", font=("Microsoft YaHei UI", 9),
                               bg=BG, fg=SUB)
        self.status.pack(side="right")

        # 日志
        self.log = tk.Text(self, height=12, relief="flat", bg="#f6f8fa", fg=FG,
                           font=("Consolas", 9), padx=10, pady=8, wrap="word")
        self.log.pack(fill="both", expand=True, pady=(0, 12), **pad)
        self.log.tag_config("dim", foreground=SUB)
        self.log.tag_config("ok", foreground="#1a7f37")
        self.log.tag_config("warn", foreground="#bf8700")
        self.log.configure(state="disabled")

        self.refresh_table()
        self.write("提示：改完配置后需要重启 WorkBuddy 才会读到新模型列表。\n",
                   "dim")

    # ---------------- UI ----------------
    def write(self, text, tag=None):
        self.log.configure(state="normal")
        self.log.insert("end", text, tag or ())
        self.log.see("end")
        self.log.configure(state="disabled")

    def refresh_scope(self):
        """刷新「影响范围」那行：这面板改的是什么、会不会碰到 Claude Code。"""
        txt, via = claude_hint()
        self.claude_via_bridge = via
        line = "只改 WorkBuddy 的 models.json，不动 Claude Code 的 settings.json / profiles。"
        if txt:
            line += "\n" + txt
            if via:
                line += "；测速会自动降到 3 并发，避免挤占它的请求。"
        self.scope.configure(text=line)

    def refresh_table(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        models = read_json(MODELS) or []
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
        st = read_json(STATE) or {}
        self.meta.configure(text="巡检 %s · 重选 %s"
                            % (fmt_ts(st.get("last_check_ts")),
                               fmt_ts(st.get("last_pick_ts"))))
        self.refresh_scope()

    def set_running(self, flag, text=""):
        self.running = flag
        for b in (self.btn_pick, self.btn_preview, self.btn_check, self.btn_undo):
            b.configure(state="disabled" if flag else "normal",
                        cursor="arrow" if flag else "hand2")
        self.status.configure(text=text or ("运行中…" if flag else "就绪"))

    # ---------------- 执行 ----------------
    def run(self, mode):
        if self.running:
            return
        # Claude Code 正走同一桥接时限流降到 3：桥接内部并发上限是 4，
        # 面板开 6 路会把它的对话请求挤到队尾。只测 WorkBuddy 时不需要让。
        jobs = "3" if self.claude_via_bridge else "6"
        if mode == "repick":
            args = [PY, SCRIPT, "--auto", "--repick", "--bench-jobs", jobs,
                    "--bench-timeout", "60"]
            title = "重新择优"
        elif mode == "preview":
            # --pick --dry-run：完整测速+验工具，排出前 5，但一个字节都不写
            args = [PY, SCRIPT, "--pick", "--dry-run", "--top", "5",
                    "--bench-jobs", jobs, "--bench-timeout", "60"]
            title = "预览"
        else:
            args = [PY, SCRIPT, "--auto", "--force", "--bench-jobs", jobs,
                    "--bench-timeout", "60"]
            title = "体检"
        self.mode = mode
        self.set_running(True, "%s中…" % title)
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.write("$ install_free_llms.py %s\n\n"
                   % " ".join(args[2:]), "dim")
        if self.claude_via_bridge:
            self.write("Claude Code 正在用同一桥接，本次并发降到 %s 路。\n\n"
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
            self.after(0, self.write, "执行失败：%s\n" % e)
            rc = -1
        self.after(0, self._done, title, rc)

    def _done(self, title, rc):
        self.set_running(False)
        self.refresh_table()
        if rc == 0:
            self.write("\n✓ %s完成\n" % title, "ok")
        else:
            self.write("\n× %s结束，退出码 %d\n" % (title, rc), "warn")
        # 预览不改配置，别提示重启；其余两种都可能动过 models.json
        if self.mode == "preview":
            if rc == 0:
                self.write("上面就是新的前 5 名。想要就点「重新择优」，"
                           "配置一个字节都没动。\n", "dim")
        else:
            self.write("重启 WorkBuddy 后生效。\n", "dim")

    # ---------------- 撤销 ----------------
    def undo(self):
        """把 models.json 恢复到最近一次自动改动之前的状态。

        为什么要有：现在配置全由脚本生成（url / apiKey / 能力位都是算出来的），
        手工改 JSON 既不现实也容易改坏，所以「改错了怎么办」必须由程序兜住。
        """
        bk, when = latest_backup()
        if not bk:
            self.write("没有可用的备份（%s 下找不到 models.json.bak-*）。\n"
                       % BACKUP_DIR, "warn")
            return
        data = read_json(bk)
        if not isinstance(data, list):
            self.write("最近的备份读不出来或不是模型列表：%s\n" % bk, "warn")
            return
        names = "、".join(str(m.get("id")) for m in data[:6] if isinstance(m, dict))
        if not messagebox.askyesno(
                "撤销改动",
                "恢复到 %s 的备份？\n\n共 %d 个模型：\n%s%s\n\n"
                "当前配置会先另存一份，撤销本身也可回退。"
                % (when, len(data), names, "…" if len(data) > 6 else ""),
                parent=self):
            return
        try:
            # 先把当前存一份，避免「撤销」变成单向操作
            safe = "%s.bak-before-undo-%s" % (MODELS, time.strftime("%Y%m%d-%H%M%S"))
            if os.path.isfile(MODELS):
                shutil.copyfile(MODELS, safe)
            shutil.copyfile(bk, MODELS)
        except Exception as e:
            self.write("恢复失败：%s\n" % e, "warn")
            return
        mark_used(bk)                       # 这份用过了，下次撤销往前推一份
        pruned = prune_backups()
        self.refresh_table()
        self.write("已恢复到 %s（%d 个模型）。\n" % (when, len(data)), "ok")
        if pruned:
            self.write("顺手清掉 %d 份旧备份（只留最近 %d 份）。\n"
                       % (pruned, MAX_BACKUPS), "dim")
        self.write("撤销前的配置另存为：%s\n" % os.path.basename(safe), "dim")
        self.write("重启 WorkBuddy 后生效。\n", "dim")


if __name__ == "__main__":
    Panel().mainloop()
