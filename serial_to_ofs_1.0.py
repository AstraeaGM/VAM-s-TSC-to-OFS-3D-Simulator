#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
串口 -> OFS_Simulator3D 桥接程序（带 GUI）
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from urllib.parse import urlparse

# ================= --noconsole 兼容 =================
class _Null:
    def write(self, *_): pass
    def flush(self, *_): pass
    def isatty(self, *_): return False

if sys.stdout is None: sys.stdout = _Null()
if sys.stderr is None: sys.stderr = _Null()

import serial
import serial.tools.list_ports
import websockets

logging.getLogger("websockets").setLevel(logging.WARNING)


# ================= 固定参数 =================
SEND_HZ = 120
MIN_DELTA = 0.01

AXIS_NAME_MAP = {
    "L0": "L0", "L1": "L1.surge", "L2": "L2.sway",
    "R0": "R0.twist", "R1": "R1.roll", "R2": "R2.pitch",
}

SIMULATOR_CANDIDATES = [
    "FunscriptSimulator3D.exe",
    "OFS_Simulator3D.exe",
]

DEFAULT_CONFIG = {
    "serial_port": "",
    "baud": 115200,
    "ws_url": "ws://127.0.0.1:8080/ofs",
    "exe_path": "",
}


# ================= 路径与配置 =================
def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

BASE_DIR = app_dir()
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k in DEFAULT_CONFIG:
                if k in data:
                    cfg[k] = data[k]
            # 兼容旧版只有 ws_port 的配置
            if "ws_port" in data and "ws_url" not in data:
                cfg["ws_url"] = f"ws://127.0.0.1:{data['ws_port']}/ofs"
        except Exception:
            pass
    return cfg


def save_config(cfg):
    data = {k: cfg.get(k, DEFAULT_CONFIG[k]) for k in DEFAULT_CONFIG}
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def url_port(url):
    try:
        u = urlparse(url)
        return u.port or (443 if u.scheme == "wss" else 80)
    except Exception:
        return 8080


def auto_detect_simulator(cfg):
    cur = cfg.get("exe_path", "")
    if cur and os.path.exists(cur):
        return cur
    for name in SIMULATOR_CANDIDATES:
        cand = os.path.join(BASE_DIR, name)
        if os.path.exists(cand):
            cfg["exe_path"] = cand
            save_config(cfg)
            return cand
    return None


# ================= 串口枚举（快速版） =================
def list_com_ports():
    found = set()
    try:
        for p in serial.tools.list_ports.comports(include_links=True):
            if p.device:
                found.add(p.device)
    except Exception:
        pass

    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DEVICEMAP\SERIALCOMM"
            ) as key:
                i = 0
                while True:
                    try:
                        _n, v, _t = winreg.EnumValue(key, i)
                        if v:
                            found.add(v)
                        i += 1
                    except OSError:
                        break
        except Exception:
            pass

    def sort_key(name):
        m = re.match(r"COM(\d+)", name, re.IGNORECASE)
        return (0, int(m.group(1))) if m else (1, name.upper())

    return sorted(found, key=sort_key)


# ================= 运行时状态 =================
class State:
    __slots__ = ("lock", "serial_port", "baud", "ws_url",
                 "exe_path", "serial_version")

    def __init__(self):
        self.lock = threading.Lock()
        self.serial_port = ""
        self.baud = 115200
        self.ws_url = DEFAULT_CONFIG["ws_url"]
        self.exe_path = ""
        self.serial_version = 0

    def apply(self, cfg):
        with self.lock:
            if cfg["serial_port"] != self.serial_port or cfg["baud"] != self.baud:
                self.serial_version += 1
            self.serial_port = cfg["serial_port"]
            self.baud = cfg["baud"]
            self.ws_url = cfg["ws_url"]
            self.exe_path = cfg["exe_path"]

    def snap(self):
        with self.lock:
            return (self.serial_port, self.baud, self.ws_url,
                    self.exe_path, self.serial_version)


# ================= 串口 -> WebSocket =================
async def client_handler(ws, state):
    port, baud, _, _, ver = state.snap()
    if not port:
        await ws.close(1011, "no serial port")
        return

    print(f"[WS] 客户端连接: {ws.remote_address} 串口={port}@{baud}")

    box = {"ser": None, "ver": ver, "port": port, "baud": baud}

    def open_ser():
        s = box["ser"]
        if s is not None:
            try: s.close()
            except Exception: pass
            box["ser"] = None
        try:
            box["ser"] = serial.Serial(box["port"], box["baud"], timeout=0.05)
            print(f"[串口] 打开 {box['port']} @ {box['baud']}")
            return True
        except Exception as e:
            print(f"[串口] 打开失败: {e}")
            box["ser"] = None
            return False

    if not open_ser():
        await ws.close(1011, "serial open failed")
        return

    try:
        await ws.send(json.dumps({
            "type": "event", "name": "play_change",
            "data": {"playing": True}
        }))
    except Exception:
        pass

    latest = {}
    last_sent = {}
    stop = asyncio.Event()
    interval = 1.0 / SEND_HZ
    pattern = re.compile(r"([LR][0-2])(\d{4})")

    async def reader():
        while not stop.is_set():
            p, b, _, _, v = state.snap()
            if v != box["ver"] or p != box["port"] or b != box["baud"]:
                box["ver"], box["port"], box["baud"] = v, p, b
                open_ser()
            if box["ser"] is None:
                await asyncio.sleep(0.5)
                open_ser()
                continue
            try:
                line = await asyncio.to_thread(box["ser"].readline)
            except Exception:
                box["ser"] = None
                await asyncio.sleep(0.3)
                continue
            if not line:
                continue
            text = line.decode("ascii", errors="ignore")
            for m in pattern.finditer(text):
                latest[m.group(1)] = int(m.group(2)) / 9999.0 * 100.0

    async def sender():
        while not stop.is_set():
            for axis, pos in latest.items():
                name = AXIS_NAME_MAP.get(axis)
                if not name:
                    continue
                prev = last_sent.get(axis)
                if prev is not None and abs(prev - pos) < MIN_DELTA:
                    continue
                try:
                    await ws.send(json.dumps({
                        "type": "event", "name": "funscript_change",
                        "data": {"name": name, "funscript": {
                            "actions": [{"at": 0, "pos": pos}]}}
                    }))
                    last_sent[axis] = pos
                except websockets.ConnectionClosed:
                    stop.set()
                    return
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    t1 = asyncio.create_task(reader())
    t2 = asyncio.create_task(sender())
    try:
        await asyncio.wait([t1, t2], return_when=asyncio.FIRST_COMPLETED)
    finally:
        stop.set()
        for t in (t1, t2):
            t.cancel()
        await asyncio.gather(t1, t2, return_exceptions=True)
        s = box["ser"]
        if s is not None:
            try: s.close()
            except Exception: pass
        print(f"[串口] 关闭 {box['port']}")


# ================= 后台 asyncio 线程 =================
class BridgeThread(threading.Thread):
    def __init__(self, state, stop_event, on_error):
        super().__init__(daemon=True, name="Bridge")
        self.state = state
        self.stop_event = stop_event
        self.on_error = on_error
        self.sim_proc = None
        self._sim_lock = threading.Lock()

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._main())
        finally:
            try: loop.close()
            except Exception: pass

    async def _main(self):
        self.start_simulator()
        try:
            await self._serve()
        finally:
            self.stop_simulator()

    # ---- 模拟器 ----
    def start_simulator(self):
        with self._sim_lock:
            _, _, ws_url, exe, _ = self.state.snap()
            if not exe or not os.path.exists(exe):
                if exe:
                    print(f"[模拟器] 找不到: {exe}")
                return
            if self.sim_proc is not None and self.sim_proc.poll() is None:
                return
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            try:
                self.sim_proc = subprocess.Popen(
                    [exe, ws_url],
                    cwd=os.path.dirname(exe),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=flags,
                )
                print(f"[模拟器] 启动: {exe} {ws_url}")
            except Exception as e:
                print(f"[模拟器] 启动失败: {e}")

    def stop_simulator(self):
        with self._sim_lock:
            p = self.sim_proc
            if p is not None and p.poll() is None:
                try: p.terminate()
                except Exception: pass
            self.sim_proc = None

    def restart_simulator(self):
        self.stop_simulator()
        self.start_simulator()

    # ---- WebSocket 服务端 ----
    async def _serve(self):
        while not self.stop_event.is_set():
            _, _, ws_url, _, _ = self.state.snap()
            port = url_port(ws_url)
            conns = set()

            async def tracked(ws):
                conns.add(ws)
                try:
                    await client_handler(ws, self.state)
                finally:
                    conns.discard(ws)

            try:
                server = await websockets.serve(
                    tracked, "0.0.0.0", port,
                    subprotocols=["ofs-api.json"],
                )
                print(f"[WS] 监听 0.0.0.0:{port} ({ws_url})")
            except OSError as e:
                if self.on_error:
                    self.on_error("端口被占用",
                        f"端口 {port} 已被占用。\n"
                        f"请修改 WebSocket 地址后点“应用”。\n\n{e}")
                # 等待 URL 变化
                while not self.stop_event.is_set():
                    await asyncio.sleep(0.5)
                    _, _, cur, _, _ = self.state.snap()
                    if url_port(cur) != port:
                        break
                continue

            # 监控 URL 变化（任何部分变化都重启）
            while not self.stop_event.is_set():
                await asyncio.sleep(0.3)
                _, _, cur, _, _ = self.state.snap()
                if cur != ws_url:
                    print("[WS] 地址变化，重启服务端和模拟器")
                    break

            server.close()
            for ws in list(conns):
                try: await ws.close(1001, "restart")
                except Exception: pass
            try:
                await asyncio.wait_for(server.wait_closed(), timeout=2)
            except asyncio.TimeoutError:
                pass

            if not self.stop_event.is_set():
                self.restart_simulator()

        print("[桥接] 已停止")


# ================= 单实例 =================
_mutex = None

def acquire_single_instance():
    global _mutex
    if os.name != "nt":
        return True
    try:
        import ctypes
        k = ctypes.windll.kernel32
        _mutex = k.CreateMutexW(None, False, "SerialToOFS_v2")
        return k.GetLastError() != 183
    except Exception:
        return True


# ================= GUI =================
class App:
    def __init__(self, root):
        self.root = root
        root.title("串口 → OFS 桥接")
        root.resizable(False, False)

        self.cfg = load_config()
        self.state = State()
        self.state.apply(self.cfg)
        self.stop_event = threading.Event()

        self._build_ui()
        self._auto_detect()
        root.after(100, self._refresh_ports)
        root.after(200, self._to_front)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.bridge = BridgeThread(self.state, self.stop_event, self._show_error)
        self.bridge.start()

    # ---- 自动检测 ----
    def _auto_detect(self):
        path = auto_detect_simulator(self.cfg)
        if path:
            self.state.apply(self.cfg)
            self.sim_name.set(os.path.basename(path))
            self.status.set(f"检测到模拟器: {os.path.basename(path)}")
        else:
            self.status.set("未检测到模拟器，请点击“选择”")

    # ---- UI ----
    def _build_ui(self):
        f = ttk.Frame(self.root, padding=12)
        f.grid(sticky="nsew")

        # 串口
        ttk.Label(f, text="串口:").grid(row=0, column=0, sticky="w", pady=4)
        self.port_var = tk.StringVar(value=self.cfg["serial_port"])
        cb = ttk.Combobox(f, textvariable=self.port_var, width=24, state="normal")
        cb.grid(row=0, column=1, sticky="w", pady=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self.apply())
        cb.bind("<Return>", lambda e: self.apply())
        cb.bind("<FocusOut>", lambda e: self.apply())
        self.port_cb = cb
        ttk.Button(f, text="刷新", command=self._refresh_ports, width=6).grid(
            row=0, column=2, padx=4)

        # 波特率
        ttk.Label(f, text="波特率:").grid(row=1, column=0, sticky="w", pady=4)
        self.baud_var = tk.StringVar(value=str(self.cfg["baud"]))
        bc = ttk.Combobox(f, textvariable=self.baud_var, width=24, state="readonly",
            values=["9600", "19200", "38400", "57600", "115200",
                    "230400", "460800", "921600"])
        bc.grid(row=1, column=1, sticky="w", pady=4)
        bc.bind("<<ComboboxSelected>>", lambda e: self.apply())

        # WebSocket 地址
        ttk.Label(f, text="WebSocket 地址:").grid(row=2, column=0, sticky="w", pady=4)
        self.wsurl_var = tk.StringVar(value=self.cfg["ws_url"])
        we = ttk.Entry(f, textvariable=self.wsurl_var, width=26)
        we.grid(row=2, column=1, sticky="w", pady=4)
        we.bind("<Return>", lambda ev: self.apply())
        we.bind("<FocusOut>", lambda ev: self.apply())

        # 模拟器
        ttk.Label(f, text="模拟器:").grid(row=3, column=0, sticky="w", pady=4)
        self.sim_name = tk.StringVar(value="(未选择)")
        ttk.Label(f, textvariable=self.sim_name, width=26,
                  foreground="#0066cc", anchor="w").grid(
            row=3, column=1, sticky="w", pady=4)
        ttk.Button(f, text="选择", command=self._choose_sim, width=6).grid(
            row=3, column=2, padx=4)

        ttk.Separator(f, orient="horizontal").grid(
            row=4, column=0, columnspan=3, sticky="we", pady=8)

        # 状态
        ttk.Label(f, text="状态:").grid(row=5, column=0, sticky="w")
        self.status = tk.StringVar(value="就绪")
        ttk.Label(f, textvariable=self.status, foreground="#008000").grid(
            row=5, column=1, columnspan=2, sticky="w")

        # 按钮
        bf = ttk.Frame(f)
        bf.grid(row=6, column=0, columnspan=3, pady=(10, 0))
        ttk.Button(bf, text="启动模拟器",
                   command=self._launch_sim).grid(row=0, column=0, padx=4)
        ttk.Button(bf, text="应用",
                   command=self.apply).grid(row=0, column=1, padx=4)

        # 底部版权信息
        ttk.Separator(f, orient="horizontal").grid(
            row=7, column=0, columnspan=3, sticky="we", pady=(10, 4))
        ttk.Label(
            f,
            text="作者：AstraeaG    邮箱：astraeag@qq.com\n开源软件，不可商用。",
            foreground="#888888",
            justify="center",
        ).grid(row=8, column=0, columnspan=3, pady=(0, 4))
        

    # ---- 串口 ----
    def _refresh_ports(self):
        ports = list_com_ports()
        self.port_cb["values"] = ports
        if not self.port_var.get().strip() and ports:
            self.port_var.set(ports[0])
            self.cfg["serial_port"] = ports[0]
            self.state.apply(self.cfg)
            save_config(self.cfg)
        self.status.set(f"检测到 {len(ports)} 个串口" if ports
                        else "未检测到串口，请手动输入")

    # ---- 模拟器 ----
    def _choose_sim(self):
        init = os.path.dirname(self.cfg.get("exe_path") or BASE_DIR)
        path = filedialog.askopenfilename(
            title="选择模拟器",
            initialdir=init,
            filetypes=[("可执行文件", "*.exe"), ("所有", "*.*")],
        )
        if not path:
            return
        self.cfg["exe_path"] = path
        self.state.apply(self.cfg)
        save_config(self.cfg)
        self.sim_name.set(os.path.basename(path))
        self.status.set(f"已选择: {os.path.basename(path)}")

    def _launch_sim(self):
        if not self.cfg.get("exe_path"):
            messagebox.showinfo("提示", "请先选择模拟器")
            return
        self.bridge.restart_simulator()
        self.status.set("模拟器已启动")

    # ---- 错误提示 ----
    def _show_error(self, title, msg):
        try:
            self.root.after(0, lambda: messagebox.showerror(title, msg))
        except Exception:
            pass

    def _to_front(self):
        try:
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after(500, lambda: self.root.attributes("-topmost", False))
            self.root.focus_force()
        except Exception:
            pass

    # ---- 应用配置 ----
    def apply(self):
        url = self.wsurl_var.get().strip()
        if not url.startswith(("ws://", "wss://")):
            messagebox.showerror("错误", "WebSocket 地址必须以 ws:// 或 wss:// 开头")
            return
        try:
            baud = int(self.baud_var.get())
        except ValueError:
            messagebox.showerror("错误", "波特率必须是整数")
            return

        self.cfg["serial_port"] = self.port_var.get().strip()
        self.cfg["baud"] = baud
        self.cfg["ws_url"] = url
        self.state.apply(self.cfg)
        save_config(self.cfg)
        self.status.set(f"已应用: {self.cfg['serial_port'] or '(未选)'} @ {baud}")

    # ---- 退出 ----
    def _on_close(self):
        self.stop_event.set()
        save_config(self.cfg)
        if self.bridge.is_alive():
            self.bridge.join(timeout=3)
        try: self.root.destroy()
        except Exception: pass


# ================= 入口 =================
def main():
    root = tk.Tk()
    try:
        s = ttk.Style()
        if "vista" in s.theme_names():
            s.theme_use("vista")
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    if not acquire_single_instance():
        try:
            r = tk.Tk()
            r.withdraw()
            messagebox.showinfo("提示", "程序已在运行")
            r.destroy()
        except Exception:
            pass
        sys.exit(0)
    main()