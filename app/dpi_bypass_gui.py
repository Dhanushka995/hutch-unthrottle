"""
Hutch Unthrottle - local DPI-bypass proxy GUI
------------------------------------------------
Wraps the open-source ByeDPI (ciadpi.exe) engine
(https://github.com/hufrea/byedpi) with a simple Connect/Disconnect
GUI for Windows.
"""

import ctypes
import os
import subprocess
import sys
import threading
import tkinter as tk
import winreg
from tkinter import messagebox, scrolledtext

APP_TITLE = "Hutch Unthrottle"
PROXY_HOST = "127.0.0.1"
PROXY_PORT = 1080

# TRY #0: ByeDPI's own recommended default Windows profile
BYEDPI_ARGS = [
    "-i", PROXY_HOST,
    "-p", str(PROXY_PORT),
    "--split", "1",
    "--disorder", "3+s",
    "--mod-http=h,d",
    "--auto=torst",
    "--tlsrec", "1+s",
]

INTERNET_SETTINGS_PATH = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
INTERNET_OPTION_SETTINGS_CHANGED = 39
INTERNET_OPTION_REFRESH = 37


def resource_path(filename: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    candidate = os.path.join(base, filename)
    if os.path.exists(candidate):
        return candidate
    alt = os.path.join(os.path.dirname(sys.executable if getattr(sys, "frozen", False) else __file__), filename)
    return alt


def notify_windows_proxy_changed():
    internet_set_option = ctypes.windll.wininet.InternetSetOptionW
    internet_set_option(0, INTERNET_OPTION_SETTINGS_CHANGED, 0, 0)
    internet_set_option(0, INTERNET_OPTION_REFRESH, 0, 0)


def set_system_proxy(enable: bool, host: str = PROXY_HOST, port: int = PROXY_PORT):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INTERNET_SETTINGS_PATH, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1 if enable else 0)
        if enable:
            winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, f"socks={host}:{port}")
            winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, "<local>")
    notify_windows_proxy_changed()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("480x360")
        self.resizable(False, False)
        self.proc = None
        self.connected = False

        tk.Label(self, text=APP_TITLE, font=("Segoe UI", 16, "bold")).pack(pady=(16, 4))
        tk.Label(
            self,
            text="Local DPI-bypass proxy for ISP speed-limited sites.\nYour IP address does not change.",
            font=("Segoe UI", 9),
            fg="#555",
            justify="center",
        ).pack(pady=(0, 12))

        self.status_var = tk.StringVar(value="● Disconnected")
        self.status_label = tk.Label(self, textvariable=self.status_var, font=("Segoe UI", 12, "bold"), fg="#b00020")
        self.status_label.pack(pady=(0, 12))

        btn_frame = tk.Frame(self)
        btn_frame.pack()
        self.connect_btn = tk.Button(btn_frame, text="Connect", width=14, height=2, command=self.on_connect, bg="#1b8a3d", fg="white")
        self.connect_btn.grid(row=0, column=0, padx=8)
        self.disconnect_btn = tk.Button(btn_frame, text="Disconnect", width=14, height=2, command=self.on_disconnect, bg="#b00020", fg="white", state=tk.DISABLED)
        self.disconnect_btn.grid(row=0, column=1, padx=8)

        tk.Label(self, text="Log", font=("Segoe UI", 9, "bold")).pack(pady=(16, 0), anchor="w", padx=16)
        self.log_box = scrolledtext.ScrolledText(self, width=58, height=9, font=("Consolas", 8))
        self.log_box.pack(padx=16, pady=(4, 12))
        self.log_box.configure(state=tk.DISABLED)

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.log(f"Ready. Proxy will listen on {PROXY_HOST}:{PROXY_PORT} when connected.")

    def log(self, msg: str):
        self.log_box.configure(state=tk.NORMAL)
        self.log_box.insert(tk.END, msg + "\n")
        self.log_box.see(tk.END)
        self.log_box.configure(state=tk.DISABLED)

    def on_connect(self):
        exe_path = resource_path("ciadpi.exe")
        if not os.path.exists(exe_path):
            messagebox.showerror(APP_TITLE, f"ciadpi.exe not found next to the application:\n{exe_path}")
            return

        self.connect_btn.config(state=tk.DISABLED)
        threading.Thread(target=self._connect_worker, args=(exe_path,), daemon=True).start()

    def _connect_worker(self, exe_path):
        try:
            self.log(f"Starting engine: {os.path.basename(exe_path)} {' '.join(BYEDPI_ARGS)}")
            creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
            self.proc = subprocess.Popen(
                [exe_path] + BYEDPI_ARGS,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
                text=True,
            )
            threading.Thread(target=self._pump_logs, daemon=True).start()

            set_system_proxy(True)
            self.connected = True
            self.status_var.set("● Connected")
            self.status_label.config(fg="#1b8a3d")
            self.disconnect_btn.config(state=tk.NORMAL)
            self.log("System proxy set. Browsing should now route through the bypass proxy.")
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Failed to start: {exc}")
            self.connect_btn.config(state=tk.NORMAL)

    def _pump_logs(self):
        if not self.proc or not self.proc.stdout:
            return
        for line in self.proc.stdout:
            self.log(line.rstrip())

    def on_disconnect(self):
        self.disconnect_btn.config(state=tk.DISABLED)
        try:
            set_system_proxy(False)
        except Exception as exc:
            self.log(f"Warning: failed to reset system proxy: {exc}")
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        self.connected = False
        self.status_var.set("● Disconnected")
        self.status_label.config(fg="#b00020")
        self.connect_btn.config(state=tk.NORMAL)
        self.log("Disconnected. System proxy reverted to normal.")

    def on_close(self):
        if self.connected:
            self.on_disconnect()
        self.destroy()


if __name__ == "__main__":
    if os.name != "nt":
        print("This tool is Windows-only (uses the Windows registry + WinINet).")
        sys.exit(1)
    App().mainloop()
