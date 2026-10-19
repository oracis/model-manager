#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Claude 模型切换器 —— 点哪个模型，就把 ~/.claude/settings.json 切到那个 profile，
并自动拉起对应的本地代理。双击桌面「Claude模型切换器.cmd」启动。

⚠️ 2026-10-19 起日常入口已合并到 `model_manager.pyw` 的第二个 Tab（桌面「模型管理器.cmd」）。
本文件保留可单独运行，但两处逻辑是同一份，改动记得同步。

链路说明（两种）：
  普通 profile : Claude Code -> ccproxy:<port> -> 上游 API
  Zen 免费模型 : Claude Code -> ccproxy:3462 -> zen_bridge:8787 -> opencode serve:41234 -> Zen

带 x_bridge 的 profile 会在切换时先确保 zen_bridge 已运行；因为 zen_bridge 要
顺带拉起 opencode serve，冷启动需要约 25-40 秒，所以切换动作放在后台线程执行，
界面用轮询显示进度，避免 GUI 假死。

删除模型：免费模型经常过期/失效。**免费模型（profile 带 x_free）是真删**——
os.remove 直接干掉，不留 backup（免费模型要么不好用、要么已失效，没有保留价值）；
只有付费模型才归档到 ~/.claude/backups/deleted/（可恢复）。删完点「刷新」或重启
本工具即可，不用改代码、不用重新部署。

列表口径（2026-10-18 起统一）：切换按钮与删除下拉**同一份动态列表**——
以 profiles 目录真实存在的 profile 为准，MODELS 只提供显示名和启动参数。
MODELS 里有登记但 profile 没了的（如 glm4），按钮隐藏并在列表底部灰字提示；
profiles 里多出来的（外部工具装的）会自动补进两个列表，能切也能删。
"""
import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import tkinter as tk
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from tkinter import messagebox, ttk

HOME = os.path.expanduser("~")
PY = r"C:\Users\DELL\.workbuddy\binaries\python\versions\3.13.12\python.exe"
CC = r"C:\Users\DELL\.workbuddy\skills\claude-code-third-party-model\scripts\ccproxy.py"
ZB = r"C:\Users\DELL\.workbuddy\skills\claude-code-third-party-model\scripts\zen_bridge.py"
ZB_PORT = 8787
ZB_SDK_PORT = 41234


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


MODELS_JSON = os.path.join(HOME, ".workbuddy", "models.json")

KILO_UPSTREAM = "https://api.kilo.ai/api/gateway/v1/chat/completions"


def _auth_token():
    """从现有 profile 里借一个 ANTHROPIC_AUTH_TOKEN。

    这个 token 只对本地 ccproxy 生效（ccproxy 用自己的 key 打上游），
    值是什么无所谓，但没有它 Claude Code 会当成未配置。
    """
    for fn in sorted(os.listdir(PROFILES_DIR) or []) if os.path.isdir(PROFILES_DIR) else []:
        if not fn.endswith(".json"):
            continue
        p = read_json(os.path.join(PROFILES_DIR, fn))
        t = ((p or {}).get("env") or {}).get("ANTHROPIC_AUTH_TOKEN")
        if t:
            return t
    return "anonymous"

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
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    obj = json.loads(payload)
                except Exception:
                    continue
                if obj.get("error"):
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


# name=显示名, profile=profiles 下文件名(无后缀), port=本地 ccproxy 端口(None=直连),
# args=ccproxy 启动参数, bridge=是否需要先拉起 zen_bridge
MODELS = [
    ("DeepSeek V4.1 Flash（免费·Zen）", "zen-ds-flash", 3462,
     "--upstream http://127.0.0.1:8787/v1/chat/completions "
     "--model deepseek-v4.1-flash-free --key anonymous", True),
    ("👑 Space Bunny Free（免费·Zen·1M·实测最快）", "zen-space-bunny", 3463,
     "--upstream http://127.0.0.1:8787/v1/chat/completions "
     "--model space-bunny-free --key anonymous", True),
    ("LL Alpha Free（免费·Zen·256k·输出64k）", "zen-ll-alpha", 3469,
     "--upstream http://127.0.0.1:8787/v1/chat/completions "
     "--model ll-alpha-free --key anonymous", True),
    ("Nemotron 3.5 Super-VL（免费·Zen·1M）", "zen-nemotron-vl", 3464,
     "--upstream http://127.0.0.1:8787/v1/chat/completions "
     "--model nemotron-3.5-super-vl-free --key anonymous", True),
    ("LongCat 2.5 Preview（免费·Zen·1M）", "zen-longcat", 3465,
     "--upstream http://127.0.0.1:8787/v1/chat/completions "
     "--model longcat-2.5-preview-free --key anonymous", True),
    ("MiMo V2.6 Flash（免费·Zen·200k）", "zen-mimo", 3467,
     "--upstream http://127.0.0.1:8787/v1/chat/completions "
     "--model mimo-v2.6-flash-free --key anonymous", True),
    ("Big Pickle（免费·Zen·200k）", "zen-big-pickle", 3468,
     "--upstream http://127.0.0.1:8787/v1/chat/completions "
     "--model big-pickle --key anonymous", True),
    ("Kilo 文本 + 读图桥接（免费）", "kilo-bridge", 3457,
     "--upstream https://api.kilo.ai/api/gateway/v1/chat/completions --model kilo-auto/free --key anonymous", False),
    ("读图 Vision（免费·可看图）", "vision", 3458,
     "--upstream https://api.kilo.ai/api/gateway/v1/chat/completions --model nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free --key anonymous", False),
    ("Kilo 快稳 LFM2.5（免费·agent首选）", "kilo-fast", 3461,
     "--upstream https://api.kilo.ai/api/gateway/v1/chat/completions --model liquid/lfm-2.5-2.6b:free --key anonymous", False),
    ("DeepSeek-V4 (付费)", "deepseek", None, "", False),
    ("GLM-4-Flash（免费·稳定）", "glm4", None, "", False),
    ("GLM-4-Flash + 读图桥接（免费）", "glm4-bridge", None, "", False),
]

DETACHED = 0x00000008  # DETACHED_PROCESS

# 打开本工具时自动连上的模型（= MODELS 里带 👑 的实测最优项）。
# 桥接不再开机自启，改为「点切换器时才起」——这个 profile 就是那条起链路的入口。
# 想改默认，改这里的 profile 名即可（须与 MODELS 里的 profile 对应）。
PREFERRED_PROFILE = "zen-space-bunny"

# 删除下拉框用的短标签（按钮显示名太长，Combobox 里放不下会被截断）：
# 精简到「模型名 + 免费/付费」，一眼可辨又短。没列到的用 fallback 逻辑补。
DEL_LABELS = {
    "zen-ds-flash": "DS V4.1 Flash 免费·Zen",
    "zen-space-bunny": "👑 Space Bunny 免费·最快",
    "zen-ll-alpha": "LL Alpha 免费·256k",
    "zen-nemotron-vl": "Nemotron VL 免费·1M",
    "zen-longcat": "LongCat 免费·1M",
    "zen-mimo": "MiMo V2.6 免费",
    "zen-big-pickle": "Big Pickle 免费",
    "deepseek":     "DeepSeek-V4 付费",
    "glm4":         "GLM-4-Flash 免费",
    "glm4-bridge":  "GLM-4-Flash 免费+读图",
    "glm41v":       "GLM-4.1V 思考 免费",
    "glm46v":       "GLM-4.6V 免费",
    "kilo-bridge":  "Kilo 文本 免费+读图",
    "kilo-fast":    "Kilo LFM2.5 免费",
    "vision":       "读图 Vision 免费",
}


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
    否则会落到 http_proxy 上被拒（本机那个 127.0.0.1:14247 代理常年失效）。"""
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
        return "桥接已在运行", True
    _spawn([PY, ZB, "--port", str(ZB_PORT), "--sdk-port", str(ZB_SDK_PORT),
            "--auto-start"], "zen-bridge.log")
    if not wait_for(lambda: port_open(ZB_PORT), timeout):
        return "桥接启动超时，见 ~/.claude/zen-bridge.log", False
    if wait_for(lambda: http_ok(ZB_PORT, "/healthz", 3), 30):
        return "桥接已启动", True
    return "桥接端口已开但健康检查未通过，见 ~/.claude/zen-bridge.log", False


def ensure_proxy(port, args, timeout=30):
    if port is None:
        return "（直连，无需代理）", True
    if port_open(port):
        return "代理已在运行", True
    if args is None:
        # 外部工具装的模型：profile 在，但启动参数没登记在本脚本里，
        # 瞎起一个空 ccproxy 反而更糟——如实告诉用户，settings 切换仍然生效。
        return ("本地代理 %d 未运行，且该模型无登记启动参数，未自动拉起；"
                "请先手动启动对应代理" % port), False
    _spawn([PY, CC, "--port", str(port)] + args.split(), "ccproxy-%d.log" % port)
    if wait_for(lambda: port_open(port), timeout):
        return "已启动代理", True
    return "代理启动超时，见 ~/.claude/ccproxy-%d.log" % port, False


def current_model():
    try:
        s = json.load(open(os.path.join(HOME, ".claude", "settings.json"), encoding="utf-8"))
        env = s.get("env", {})
        return env.get("ANTHROPIC_BASE_URL", ""), (s.get("model") or env.get("ANTHROPIC_MODEL", ""))
    except Exception:
        return "", ""


PROFILES_DIR = os.path.join(HOME, ".claude", "profiles")
DELETE_DIR = os.path.join(HOME, ".claude", "backups", "deleted")


def list_profile_names():
    """profiles 目录下所有 profile 名（不含 .json）。"""
    try:
        return sorted(f[:-5] for f in os.listdir(PROFILES_DIR)
                      if f.endswith(".json") and os.path.isfile(os.path.join(PROFILES_DIR, f)))
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
    没登记」的情况。返回 (显示名, profile, 端口, 启动参数, 是否需桥接)；
    启动参数填 None=不知道，切换时不瞎拉起代理（见 ensure_proxy）。
    """
    try:
        p = json.load(open(os.path.join(PROFILES_DIR, name + ".json"), encoding="utf-8"))
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
    # 靠它在切换时自动拉起代理。读不到才返回 None（外部工具装的，参数未知）。
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
    # 读图桥接：profile 带 mcpServers 就注入，否则清掉（避免非桥接 profile 残留 MCP）
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
            # 桥接起来了，ccproxy 才可能连得上上游
            pmsg, pok = ensure_proxy(port, args)
            notes.append(pmsg)
            ok = ok and pok
    else:
        pmsg, pok = ensure_proxy(port, args)
        notes.append(pmsg)
        ok = pok
    return "; ".join(notes), ok


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Claude 模型切换器")
        # 高度不再写死 915：小屏会顶到任务栏，底部「删除模型」区被切掉
        scr_h = self.winfo_screenheight()
        self.geometry("430x%d" % min(915, max(560, scr_h - 140)))
        self.minsize(400, 460)      # 允许拖动改大小，但别拖到看不清
        self.resizable(True, True)
        tk.Label(self, text="点哪个模型，就切到哪个\n切完重启 Claude Code 即可",
                 font=("Microsoft YaHei", 11), pady=6).pack()

        self.status = tk.Label(self, text="", fg="green", font=("Microsoft YaHei", 9),
                               wraplength=390, justify="left", height=3)
        self.status.pack(pady=4)

        # ---- 模型按钮区：可滚动（模型再多也只是列表变长，滚轮/滚动条都能看全）----
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

        # MODELS 里有登记、但 profile 已不存在的条目提示（点不了也删不掉，如实说）
        self.missing_label = tk.Label(self.list_frame, text="", fg="gray",
                                      font=("Microsoft YaHei", 9),
                                      wraplength=360, justify="left")

        # 切换按钮和删除下拉共用同一份动态列表：能切的都能删，能删的都能切
        self.models = []
        self.buttons = []
        self.missing = []
        self.rebuild_models()
        self.rebuild_buttons()

        # 滚轮（Windows/delta 与 X11/Button-4,5 两套事件都接），PgUp/PgDn 翻页
        self.bind_all("<MouseWheel>", self._on_mousewheel)
        self.bind_all("<Button-4>", self._on_mousewheel)
        self.bind_all("<Button-5>", self._on_mousewheel)
        self.bind("<Prior>", lambda e: self._scroll_page(-1))
        self.bind("<Next>", lambda e: self._scroll_page(1))

        # ---- 删除模型（免费模型失效时一键移除入口）----
        sep = tk.Frame(self, height=2, bd=1, relief="sunken")
        sep.pack(fill="x", padx=26, pady=8)
        tk.Label(self, text="删除模型（免费模型真删·付费归档可恢复）",
                 font=("Microsoft YaHei", 9), fg="gray").pack()
        # Combobox 独占一行全宽（放按钮旁边会被挤到 200px，文字全截断）
        self.del_combo = ttk.Combobox(self, state="readonly", height=12,
                                      font=("Microsoft YaHei", 10))
        self.del_combo.pack(fill="x", padx=26, pady=4)
        btns = tk.Frame(self)
        btns.pack(fill="x", padx=26, pady=2)
        tk.Button(btns, text="刷新列表", width=10,
                  command=self.refresh).pack(side="left")
        tk.Button(btns, text="＋ 新建模型", width=12,
                  command=self.on_new).pack(side="left", padx=6)
        tk.Button(btns, text="删除所选模型", width=14, fg="darkred",
                  command=self.on_delete).pack(side="right")
        # 多余高度由上面的可滚动 body 吸收（原来那个纯占位 Frame 已不需要）

        self._task = None
        self._del_map = {}  # 下拉显示文本 -> profile 名
        self.refresh()
        # 打开即自动连上皇冠模型（静默，不弹框）；失败也不打扰，照常等用户手点
        self._auto_after = self.after(400, self._auto_connect)
        # 400ms 内关窗时先取消定时器，否则会报 invalid command name
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        try:
            self.after_cancel(self._auto_after)
        except Exception:
            pass
        self.destroy()

    # ---------- 滚动区 ----------
    def _on_list_configure(self, _e=None):
        """内层尺寸变了 → 更新滚动范围，并决定要不要显示滚动条。"""
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._update_scrollbar()

    def _on_canvas_configure(self, e):
        """内层宽度跟随 canvas，按钮才能横向铺满（否则拖窗口时右侧留白）。"""
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
            # 模型少到一屏放得下就隐藏，省出横向空间
            self.vsb.pack_forget()
            self.canvas.yview_moveto(0)

    def _wheel_here(self):
        """指针是否落在模型列表上（排除「删除模型」下拉框，免得抢它自己的滚动）。"""
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

    # ---------- 动态模型列表（切换按钮与删除下拉的唯一数据源）----------
    def rebuild_models(self):
        """重新拉取「现在到底有哪些模型」。

        规则：
        - MODELS 里登记了、profile 也还在 → 正常条目（带启动参数）
        - MODELS 里登记了、profile 没了 → 记入 self.missing，列表里不显示
        - MODELS 没登记、profile 却存在 → 补一个推断条目（能切也能删）
        这样「可切换」和「可删除」永远是一批人。
        """
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
        # 先解除提示标签的布局，否则重排的按钮会插到它上面（它会被顶到列表最顶端）
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
                text="未安装、已从列表隐藏：%s" % "、".join(self.missing))
            self.missing_label.pack(fill="x", padx=26, pady=(4, 0))
        else:
            self.missing_label.pack_forget()
        self.update_idletasks()

    def _auto_connect(self):
        """启动时静默切到 PREFERRED_PROFILE，把桥接和网关一并拉起。"""
        for disp, prof, port, args, bridge in MODELS:
            if prof != PREFERRED_PROFILE:
                continue
            if not os.path.isfile(os.path.join(PROFILES_DIR, prof + ".json")):
                return
            self.on_click(disp, prof, port, args, bridge, quiet=True)
            return

    def refresh_del_list(self):
        """下拉列表 = self.models 的 profile 集合（与切换按钮严格一致）。

        显示用短标签（DEL_LABELS），带 [profile名] 后缀，分得清免费/付费。
        """
        self._del_map = {}
        labels = []
        for _disp, prof, _port, _args, _bridge in self.models:
            label = DEL_LABELS.get(prof)
            if not label:
                # DEL_LABELS 没列到的（新加的 profile）：用模型 ID 短名补充
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
            return None, "模型 ID 里没有可用字符"
        dst = os.path.join(PROFILES_DIR, name + ".json")
        if os.path.isfile(dst):
            return None, "已经有同名 profile：%s.json" % name

        prof = {
            "$schema": "https://json.schemastore.org/claude-code-settings.json",
            "x_note": "由模型管理器新建（%s）"
                      % ("Zen 桥接" if via_bridge else "Kilo 直连"),
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
        dlg.title("新建模型（Claude Code）")
        dlg.transient(self)
        dlg.geometry("780x620")
        dlg.resizable(True, True)
        dlg.configure(bg="white")
        f = {"font": ("Microsoft YaHei", 10)}

        # ---- 从目录里挑：列表带性能数据，代替原来那截显示不全的下拉 ----
        top = tk.Frame(dlg, bg="white")
        top.pack(fill="x", padx=18, pady=(14, 2))
        tk.Label(top, text="从可用免费模型里选（点一行自动填到下面）",
                 bg="white", font=("Microsoft YaHei", 10)).pack(side="left")
        lbl_scan = tk.Label(top, text="", bg="white", fg="gray",
                            font=("Microsoft YaHei", 8))
        lbl_scan.pack(side="right")

        bar = tk.Frame(dlg, bg="white")
        bar.pack(fill="x", padx=18, pady=(0, 4))
        b_scan = tk.Button(bar, text="刷新目录", width=10,
                           font=("Microsoft YaHei", 9))
        b_bench = tk.Button(bar, text="测速排序", width=10,
                            font=("Microsoft YaHei", 9),
                            bg=ACCENT, fg="white", relief="flat",
                            disabledforeground="#edf2fe")
        b_scan.pack(side="left")
        b_bench.pack(side="left", padx=8)
        tk.Label(bar, text="「测速排序」逐个量首字耗时（约半分钟），灰=未测 红=挂",
                 bg="white", fg="gray", font=("Microsoft YaHei", 8)).pack(
            side="left", padx=4)

        cands = []
        bench = load_bench_cache()
        installed = {m.get("id") for m in (read_json(MODELS_JSON) or [])}

        cols = ("mid", "src", "ctx", "inst", "ttft")
        wrap = tk.Frame(dlg, bg="white")
        wrap.pack(fill="both", expand=True, padx=18, pady=(6, 0))
        tree = ttk.Treeview(wrap, columns=cols, show="headings", height=10)
        for cid, txt, w, anch in (
                ("mid", "模型 ID", 330, "w"),
                ("src", "来源", 52, "center"),
                ("ctx", "上下文", 64, "e"),
                ("inst", "已装", 48, "center"),
                ("ttft", "首字实测", 78, "e")):
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
                    ttft, tag = "超时/挂", "fail"
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
                           text=("测速中 %d/%d" % (n, len(cands)) if benching
                                 else "测速排序"))
            b_scan.config(text="扫描中" if scanning else "刷新目录")

        def scan():
            set_btns(scanning=True)
            lbl_scan.config(text="正在拉 Kilo / Zen 目录…")

            def work():
                got = scan_candidates()

                def done():
                    set_btns()
                    cands[:] = got
                    render()
                    if got:
                        lbl_scan.config(text="共 %d 个" % len(got))
                    else:
                        lbl_scan.config(text="没扫到（网络问题？可手填）")
                dlg.after(0, done)
            threading.Thread(target=work, daemon=True).start()
        b_scan.config(command=scan)

        def do_bench():
            if not cands:
                lbl_scan.config(text="目录还是空的，先「刷新目录」")
                return
            set_btns(benching=True, n=0)

            def prog(i, total, mid, ttft):
                def step():
                    b_bench.config(text="测速中 %d/%d" % (i, total))
                dlg.after(0, step)

            def work():
                nonlocal bench
                bench = bench_candidates(cands, progress=prog)

                def done():
                    set_btns()
                    render()
                    ok = sum(1 for c in cands
                             if (bench.get(c[0]) or {}).get("ttft"))
                    lbl_scan.config(text="测速完成 %d/%d 有响应，已按快慢排"
                                    % (ok, len(cands)))
                dlg.after(0, done)
            threading.Thread(target=work, daemon=True).start()
        b_bench.config(command=do_bench)

        tk.Label(dlg, text="模型 ID（也可以直接手写）", bg="white",
                 anchor="w").pack(fill="x", padx=18, pady=(10, 2))
        e_id = tk.Entry(dlg, **f)
        e_id.pack(fill="x", padx=18)

        hint = tk.Label(dlg, text="", bg="white", fg=ACCENT,
                        font=("Microsoft YaHei", 9), anchor="w")
        hint.pack(fill="x", padx=18, pady=(0, 8))

        row = tk.Frame(dlg, bg="white")
        row.pack(fill="x", padx=18, pady=2)
        tk.Label(row, text="本地代理端口", bg="white",
                 font=("Microsoft YaHei", 10)).pack(side="left")
        e_port = tk.Entry(row, width=8, **f)
        e_port.insert(0, str(self.next_port()))
        e_port.pack(side="left", padx=8)
        tk.Label(row, text="（自动挑一个没占用的）", bg="white", fg="gray",
                 font=("Microsoft YaHei", 8)).pack(side="left")

        def upd(*_a):
            mid = e_id.get().strip()
            if not mid:
                hint.config(text="")
            elif "/" in mid:
                hint.config(text="→ 走 Kilo 网关直连（ID 带斜杠）")
            else:
                hint.config(text="→ 走 Zen 免费，经本地桥接 8787")
        e_id.bind("<KeyRelease>", upd)
        upd()

        def do_create():
            mid = e_id.get().strip()
            if not mid:
                messagebox.showwarning("缺模型 ID", "先填模型 ID", parent=dlg)
                return
            try:
                port = int(e_port.get().strip())
            except ValueError:
                messagebox.showwarning("端口不对", "端口要填数字", parent=dlg)
                return
            name, args = self.create_profile(mid, port)
            if name is None:
                messagebox.showerror("创建失败", args, parent=dlg)
                return
            dlg.destroy()
            self.refresh()
            # 创建完直接切过去，顺带把代理（Zen 的还有桥接）拉起来
            self.on_click(mid, name, port, args, "/" not in mid)

        bar = tk.Frame(dlg, bg="white")
        bar.pack(fill="x", padx=18, pady=16)
        tk.Button(bar, text="取消", width=10, command=dlg.destroy).pack(side="right")
        tk.Button(bar, text="创建并切换", width=14, bg=ACCENT, fg="white",
                  relief="flat", font=("Microsoft YaHei", 10),
                  command=do_create).pack(side="right", padx=8)
        e_id.focus_set()
        dlg.grab_set()
        scan()      # 打开就自动拉目录，不用再手点「刷新目录」

    def on_delete(self):
        text = self.del_combo.get().strip()
        name = getattr(self, "_del_map", {}).get(text, text)
        if not name:
            messagebox.showwarning("没有选中", "先在下拉框里选一个要删除的模型")
            return
        try:
            prof = json.load(open(os.path.join(PROFILES_DIR, name + ".json"),
                                  encoding="utf-8"))
        except Exception as e:
            messagebox.showerror("读不了这个 profile", str(e))
            return
        model = prof.get("model") or (prof.get("env") or {}).get("ANTHROPIC_MODEL", "?")
        is_free = bool(prof.get("x_free"))
        if is_free:
            body = ("将【永久删除】免费模型「%s」\nprofile 文件: %s\n上游模型 ID: %s\n\n"
                    "免费模型直接真删，不留 backup。\n确定删除？" % (text, name + ".json", model))
        else:
            body = ("将删除模型「%s」\nprofile 文件: %s\n上游模型 ID: %s\n\n"
                    "操作 = 把该 profile 移入\n~/.claude/backups/deleted/\n"
                    "（不是硬删，需要时可捡回）\n\n确定删除？"
                    % (text, name + ".json", model))
        if not messagebox.askyesno("确认删除", body):
            return
        try:
            dest, real_delete = delete_profile(name, permanent=is_free)
        except Exception as e:
            messagebox.showerror("删除失败", str(e))
            return
        # 如果删的是当前生效的 profile，提醒一句（settings.json 本身不动，
        # 只是从此没有 profile 与它对应，显示会变成"未知"）。
        cur_base, cur_model = current_model()
        pbase = (prof.get("env") or {}).get("ANTHROPIC_BASE_URL", "")
        pmodel = prof.get("model") or (prof.get("env") or {}).get("ANTHROPIC_MODEL", "")
        extra = ""
        if pbase == cur_base and pmodel == cur_model:
            extra = ("\n\n注意：这是当前正在用的模型。settings.json 未改动，"
                     "但已无 profile 与它对应，建议再点一个别的模型。")
        self.refresh_del_list()
        self.refresh()
        if real_delete:
            done_msg = "「%s」已永久删除（免费模型，无 backup）。" % text
        else:
            done_msg = "「%s」已移除。\n归档到：%s（可恢复）" % (text, dest)
        messagebox.showinfo(
            "已删除",
            "%s%s\n\n重启 Claude Code 后，切换器列表不再显示它。"
            % (done_msg, extra))

    def refresh(self):
        # 磁盘上的 profile 可能刚被外部工具增删过，先重拉列表再匹配当前模型
        self.rebuild_models()
        self.rebuild_buttons()
        cur_base, cur_model = current_model()
        try:
            s = json.load(open(os.path.join(HOME, ".claude", "settings.json"), encoding="utf-8"))
            has_bridge = "vision-bridge" in s.get("mcpServers", {})
        except Exception:
            has_bridge = False
        cur_disp = "未知"
        for disp, prof, port, args, bridge in self.models:
            try:
                p = json.load(open(os.path.join(HOME, ".claude", "profiles", prof + ".json"), encoding="utf-8"))
            except Exception:
                continue
            pbase = p["env"]["ANTHROPIC_BASE_URL"]
            pmodel = p.get("model") or p["env"].get("ANTHROPIC_MODEL", "")
            pbridge = "vision-bridge" in p.get("mcpServers", {})
            if pbase == cur_base and pmodel == cur_model and pbridge == has_bridge:
                cur_disp = disp
                break
        self.status.config(
            text="当前：%s\n切换后重启 Claude Code；想保留上下文重启时加 --continue" % cur_disp,
            fg="blue")
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
                "该模型已删除",
                "「%s」的 profile 已被删除。\n点下方「刷新列表」或重启本工具。"
                % disp)
            self.refresh_del_list()
            return
        self._task = {"disp": disp, "prof": prof, "port": port, "args": args,
                      "bridge": bridge, "quiet": quiet, "done": False,
                      "result": None}
        tip = ("正在拉起 Zen 桥接（约 30 秒，含 opencode serve 冷启动）..."
               if bridge else "正在切换...")
        self._set_busy(True, tip, "#b8860b")
        threading.Thread(target=self._worker, daemon=True).start()
        self.after(300, self._poll)

    def _worker(self):
        t = self._task
        try:
            t["result"] = switch(t["prof"], t["port"], t["args"], t["bridge"])
        except Exception as e:
            t["result"] = ("失败：%s" % e, False)
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
            self._set_busy(False, ("已自动连上 %s（%s）" % (t["disp"], msg)) if ok
                            else ("自动连接失败：%s" % msg),
                            "green" if ok else "darkred")
            self._task = None
            self.refresh()
            return
        # 取该 profile 真实的模型 ID（原来硬编码 deepseek-v4.1-flash-free，换模型就错了）
        try:
            pf = json.load(open(os.path.join(PROFILES_DIR, t["prof"] + ".json"),
                                encoding="utf-8"))
            mid = pf.get("model") or (pf.get("env") or {}).get("ANTHROPIC_MODEL", "")
        except Exception:
            mid = ""
        extra = ""
        if t["bridge"] and ok and mid:
            extra = ("\n\n桥接已就绪，WorkBuddy 侧也能用同一个模型：\n"
                     "模型列表选 vendor=opencode 的 %s" % mid)
        messagebox.showinfo(
            "已切换" if ok else "部分失败",
            "已切换到：%s\n%s%s\n\n重启 Claude Code 后生效。\n"
            "想接着之前的对话继续：重启时用 claude --continue\n"
            "（换模型不会丢会话，历史都在 ~/.claude/projects/）"
            % (t["disp"], msg, extra))
        self._task = None
        self.refresh()


if __name__ == "__main__":
    try:
        App().mainloop()
    except Exception:
        import traceback
        log = os.path.join(HOME, ".claude", "switcher-error.log")
        with open(log, "a", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        try:
            messagebox.showerror("切换器出错", "详情见 " + log)
        except Exception:
            pass
