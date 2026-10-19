#!/usr/bin/env pythonw
# -*- coding: utf-8 -*-
"""zen_bridge_supervisor.pyw —— 无窗口守护，保证本地 Zen 桥接（8787）一直在线。

为什么需要它
------------
WorkBuddy 里的 space-bunny-free、deepseek-v4.1-flash-free 指向
http://127.0.0.1:8787。桥接进程一退出（重启电脑、会话结束、崩溃），
这两个模型立刻变成连接失败。这个守护每 20 秒探一次 /health，不通就
重新拉起桥接；--auto-start 让 opencode serve（41234）也被一并拉起。

用法
----
    pythonw zen_bridge_supervisor.pyw           # 常驻（登录自启走的就是这条）
    python  zen_bridge_supervisor.pyw --status  # 只看状态，不启动
    python  zen_bridge_supervisor.pyw --stop    # 让守护在下一轮退出

日志：~/.workbuddy/logs/zen_bridge.log（超过 5 MB 自动切成 .1）
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

HOME = os.path.expanduser("~")
PY = os.path.join(HOME, ".workbuddy", "binaries", "python",
                  "versions", "3.13.12", "python.exe")
BRIDGE = os.path.join(HOME, ".workbuddy", "skills",
                      "claude-code-third-party-model", "scripts",
                      "zen_bridge.py")
LOGDIR = os.path.join(HOME, ".workbuddy", "logs")
LOG = os.path.join(LOGDIR, "zen_bridge.log")
LOCK_PORT = 48787          # 单实例锁：能 bind 上说明没别人在守
BRIDGE_PORT = 8787
SDK_PORT = 41234
CHECK_GAP = 20             # 健康检查间隔（秒）
START_GRACE = 25           # 启动后给多少秒等它变健康
MAX_LOG = 5 * 1024 * 1024

CREATE_NO_WINDOW = 0x08000000
DETACHED = 0x00000008


def log(msg):
    try:
        os.makedirs(LOGDIR, exist_ok=True)
        if os.path.exists(LOG) and os.path.getsize(LOG) > MAX_LOG:
            bak = LOG + ".1"
            if os.path.exists(bak):
                os.remove(bak)
            os.replace(LOG, bak)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def healthy(timeout=3.0):
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/health" % BRIDGE_PORT,
                timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def port_open(port, timeout=1.0):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def listen_pid(port):
    """从 netstat -ano 里找出监听该端口的 PID（只认 LISTENING 行）。"""
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                             capture_output=True, text=True,
                             creationflags=CREATE_NO_WINDOW).stdout
    except Exception:
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 5 or "LISTEN" not in parts[3].upper():
            continue
        if parts[1].endswith(":%d" % port):
            return parts[4]
    return None


def spawn_bridge():
    if not os.path.exists(BRIDGE):
        log("找不到桥接脚本：%s" % BRIDGE)
        return None
    os.makedirs(LOGDIR, exist_ok=True)
    out = open(LOG, "a", encoding="utf-8", buffering=1)
    cmd = [PY, BRIDGE, "--port", str(BRIDGE_PORT), "--sdk-port", str(SDK_PORT),
           "--auto-start", "--autopick"]
    flags = CREATE_NO_WINDOW | DETACHED
    p = subprocess.Popen(cmd, cwd=os.path.dirname(BRIDGE),
                         stdout=out, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, creationflags=flags)
    log("拉起桥接 pid=%d（opencode serve %d 自动启动）" % (p.pid, SDK_PORT))
    return p


def stop(proc):
    if not proc:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def main():
    status_only = "--status" in sys.argv

    if status_only:
        print("桥接 8787：%s" % ("在线" if healthy() else "离线"))
        print("opencode serve %d：%s"
              % (SDK_PORT, "在线" if port_open(SDK_PORT) else "离线"))
        return 0

    # 单实例：抢不到锁说明已经有守护在跑，直接退出
    lock = socket.socket()
    lock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        lock.bind(("127.0.0.1", LOCK_PORT))
        lock.listen(1)
    except OSError:
        log("已有守护实例在运行，本进程退出")
        return 0

    log("守护启动：每 %ds 检查一次" % CHECK_GAP)
    proc = None
    fails = 0
    while True:
        alive = healthy()
        proc_dead = proc is not None and proc.poll() is not None
        if not alive:
            if proc_dead:
                log("桥接子进程已退出（code=%s）" % proc.returncode)
                proc = None
            # 端口有人但 health 不通 → 卡死。只杀占着这个端口的那个 PID，
            # 绝不能按进程名杀（会把 agent 自己的 python 一起带走）。
            if proc is None and port_open(BRIDGE_PORT):
                pid = listen_pid(BRIDGE_PORT)
                if pid:
                    log("8787 被 pid=%s 占用但健康检查失败，清理它" % pid)
                    try:
                        subprocess.run(["taskkill", "/F", "/PID", pid],
                                       capture_output=True,
                                       creationflags=CREATE_NO_WINDOW)
                        time.sleep(1.5)
                    except Exception as e:
                        log("清理失败：%s" % e)
            if proc is None:
                proc = spawn_bridge()
                t0 = time.time()
                while time.time() - t0 < START_GRACE:
                    if healthy(2.0):
                        log("桥接已就绪（%.1fs）" % (time.time() - t0))
                        fails = 0
                        break
                    time.sleep(1.5)
                else:
                    fails += 1
                    log("桥接启动未就绪（第 %d 次失败）" % fails)
                    stop(proc)
                    proc = None
                    time.sleep(min(5 * fails, 30))
                    continue
        else:
            fails = 0
        time.sleep(CHECK_GAP)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
