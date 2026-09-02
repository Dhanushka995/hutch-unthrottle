"""
Hutch Unthrottle v2 - local DPI-bypass proxy GUI with a built-in
argument lab (no rebuild needed to try new ByeDPI parameters).

Wraps the open-source ByeDPI (ciadpi.exe) engine
(https://github.com/hufrea/byedpi).

Key upgrade over v1: you can type any ciadpi arguments directly into
the app and hit Connect - no GitHub push / rebuild / redownload cycle
needed to test a new combination. Also includes:
  - A traceroute helper to find how many hops away the ISP's DPI
    equipment likely sits, so you can pick a sane --ttl value instead
    of guessing blind.
  - An automatic connectivity self-check a couple of seconds after
    connecting: if the chosen arguments break the connection entirely,
    the app reverts the system proxy automatically instead of leaving
    you offline.
"""

import ctypes
import os
import re
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import winreg
from tkinter import messagebox, scrolledtext, ttk

try:
    import socks  # PySocks - used only for the connectivity self-check
    HAVE_SOCKS = True
except ImportError:
    HAVE_SOCKS = False

APP_TITLE = "Hutch Unthrottle"
PROXY_HOST = "127.0.0.1"
PROXY_PORT = 1080

# Each preset is just the "desync" part of the ciadpi command line.
# -i/-p (listen address/port) are always added automatically by the code.
PRESETS = {
    "Default (split+disorder)": "--split 1 --disorder 3+s --mod-http=h,d --auto=torst --tlsrec 1+s",
    "OOB only (no fake)": "--disorder 1 --oob 25+s --mod-http=h,d",
    "OOB high offset": "--disorder 2 --oob 40+s --mod-http=h,d",
    "Fake + low TTL": "--disorder 1 --fake -1 --ttl 4",
    "Fake + mid TTL": "--disorder 1 --fake -1 --ttl 10",
    "Fake + high TTL": "--disorder 1 --fake -1 --ttl 14",
    "Fake + high TLSrec (no ttl)": "--disorder 1 --fake -1 --tlsrec 25+s",
    "Split + high TLSrec + mod-http": "--split 2+s --tlsrec 24+s --mod-http=h,d --disorder 1",
}

INTERNET_SETTINGS_PATH = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
INTERNET_OPTION_SETTINGS_CHANGED = 39
INTERNET_OPTION_REFRESH = 37


def resource_path(filename: str) -> str:
    """Find a bundled file whether running from source or a PyInstaller exe."""
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
        self.geometry("640x560")
        self.resizable(False, False)
        self.proc = None
        self.connected = False

        tk.Label(self, text=APP_TITLE, font=("Segoe UI", 16, "bold")).pack(pady=(14, 2))
        tk.Label(
            self,
            text="Local DPI-bypass proxy for ISP speed-limited sites. Your IP does not change.",
            font=("Segoe UI", 9), fg="#555",
        ).pack(pady=(0, 10))

        self.status_var = tk.StringVar(value="● Disconnected")
        self.status_label = tk.Label(self, textvariable=self.status_var, font=("Segoe UI", 12, "bold"), fg="#b00020")
        self.status_label.pack(pady=(0, 10))

        # --- Preset + args row ---
        preset_frame = tk.Frame(self)
        preset_frame.pack(padx=16, fill="x")
        tk.Label(preset_frame, text="Preset:", font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w")
        self.preset_var = tk.StringVar(value=list(PRESETS.keys())[1])
        preset_menu = ttk.Combobox(preset_frame, textvariable=self.preset_var, values=list(PRESETS.keys()), state="readonly", width=40)
        preset_menu.grid(row=0, column=1, sticky="w", padx=(6, 0))
        preset_menu.bind("<<ComboboxSelected>>", self.on_preset_selected)

        tk.Label(preset_frame, text="ciadpi arguments (editable):", font=("Segoe UI", 9, "bold")).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 2))
        self.args_var = tk.StringVar(value=PRESETS[self.preset_var.get()])
        args_entry = tk.Entry(preset_frame, textvariable=self.args_var, font=("Consolas", 9), width=70)
        args_entry.grid(row=2, column=0, columnspan=2, sticky="we")

        # --- Connect / Disconnect ---
        btn_frame = tk.Frame(self)
        btn_frame.pack(pady=10)
        self.connect_btn = tk.Button(btn_frame, text="Connect", width=14, height=2, command=self.on_connect, bg="#1b8a3d", fg="white")
        self.connect_btn.grid(row=0, column=0, padx=8)
        self.disconnect_btn = tk.Button(btn_frame, text="Disconnect", width=14, height=2, command=self.on_disconnect, bg="#b00020", fg="white", state=tk.DISABLED)
        self.disconnect_btn.grid(row=0, column=1, padx=8)

        # --- Diagnostics ---
        diag_frame = tk.Frame(self)
        diag_frame.pack(pady=(0, 6))
        tk.Button(diag_frame, text="Traceroute to YouTube", command=self.on_traceroute).grid(row=0, column=0, padx=6)
        self.test_btn = tk.Button(diag_frame, text="Test connectivity now", command=self.on_manual_test, state=(tk.NORMAL if HAVE_SOCKS else tk.DISABLED))
        self.test_btn.grid(row=0, column=1, padx=6)

        tk.Label(self, text="Log", font=("Segoe UI", 9, "bold")).pack(pady=(6, 0), anchor="w", padx=16)
        self.log_box = scrolledtext.ScrolledText(self, width=78, height=15, font=("Consolas", 8))
        self.log_box.pack(padx=16, pady=(4, 12))
        self.log_box.configure(state=tk.DISABLED)

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.log(f"Ready. Proxy will listen on {PROXY_HOST}:{PROXY_PORT} when connected.")
        if not HAVE_SOCKS:
            self.log("Note: 'pysocks' not installed - auto/manual connectivity self-check is disabled.")

    # ---------- logging ----------
    def log(self, msg: str):
        self.log_box.configure(state=tk.NORMAL)
        self.log_box.insert(tk.END, msg + "\n")
        self.log_box.see(tk.END)
        self.log_box.configure(state=tk.DISABLED)

    def on_preset_selected(self, _event=None):
        self.args_var.set(PRESETS[self.preset_var.get()])

    # ---------- connect / disconnect ----------
    def on_connect(self):
        exe_path = resource_path("ciadpi.exe")
        if not os.path.exists(exe_path):
            messagebox.showerror(APP_TITLE, f"ciadpi.exe not found next to the application:\n{exe_path}")
            return
        self.connect_btn.config(state=tk.DISABLED)
        threading.Thread(target=self._connect_worker, args=(exe_path,), daemon=True).start()

    def _connect_worker(self, exe_path):
        try:
            desync_args = self.args_var.get().split()
            full_args = ["-i", PROXY_HOST, "-p", str(PROXY_PORT)] + desync_args
            self.log(f"Starting engine: ciadpi.exe {' '.join(full_args)}")
            creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
            self.proc = subprocess.Popen(
                [exe_path] + full_args,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=creationflags, text=True,
            )
            threading.Thread(target=self._pump_logs, daemon=True).start()
            time.sleep(0.5)  # let ciadpi bind its listening socket

            if self.proc.poll() is not None:
                self.log("Engine exited immediately - these arguments are invalid or unsupported. Reverted.")
                self.connect_btn.config(state=tk.NORMAL)
                return

            set_system_proxy(True)
            self.connected = True
            self.status_var.set("● Connected")
            self.status_label.config(fg="#1b8a3d")
            self.disconnect_btn.config(state=tk.NORMAL)
            self.log("System proxy set.")

            if HAVE_SOCKS:
                self.log("Running automatic safety check in 2s...")
                threading.Thread(target=self._auto_safety_check, daemon=True).start()
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Failed to start: {exc}")
            self.connect_btn.config(state=tk.NORMAL)

    def _auto_safety_check(self):
        time.sleep(2)
        ok = self._proxy_connectivity_test("www.google.com", 443, timeout=6)
        if ok:
            self.log("Safety check passed: basic connectivity through the proxy works.")
        else:
            self.log("Safety check FAILED: these arguments broke connectivity entirely. Auto-reverting...")
            self.after(0, self.on_disconnect)

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

    # ---------- diagnostics ----------
    def _proxy_connectivity_test(self, host, port, timeout=5) -> bool:
        if not HAVE_SOCKS:
            return True
        try:
            s = socks.socksocket()
            s.set_proxy(socks.SOCKS5, PROXY_HOST, PROXY_PORT)
            s.settimeout(timeout)
            s.connect((host, port))
            s.close()
            return True
        except Exception:
            return False

    def on_manual_test(self):
        threading.Thread(target=self._manual_test_worker, daemon=True).start()

    def _manual_test_worker(self):
        self.log("Testing connectivity through the proxy (www.google.com:443)...")
        start = time.time()
        ok = self._proxy_connectivity_test("www.google.com", 443, timeout=6)
        elapsed = time.time() - start
        if ok:
            self.log(f"OK - connected in {elapsed:.2f}s.")
        else:
            self.log(f"FAILED after {elapsed:.2f}s. These arguments are likely breaking the connection.")

    def on_traceroute(self):
        self.log("Running traceroute to www.youtube.com (this takes ~10-20s)...")
        threading.Thread(target=self._traceroute_worker, daemon=True).start()

    def _traceroute_worker(self):
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
            proc = subprocess.run(
                ["tracert", "-d", "-h", "20", "-w", "600", "www.youtube.com"],
                capture_output=True, text=True, timeout=40, creationflags=creationflags,
            )
            output = proc.stdout or proc.stderr
            hop_numbers = re.findall(r"^\s*(\d+)\s", output, re.MULTILINE)
            for line in output.splitlines():
                self.log(line)
            if hop_numbers:
                max_hop = max(int(h) for h in hop_numbers)
                self.log(f"---> Total hops to YouTube: ~{max_hop}. "
                         f"Try --ttl values around {max(1, max_hop - 3)} to {max_hop} "
                         f"in the 'Fake + ...' presets and edit the number in the args box.")
        except Exception as exc:
            self.log(f"Traceroute failed: {exc}")


if __name__ == "__main__":
    if os.name != "nt":
        print("This tool is Windows-only (uses the Windows registry + WinINet).")
        sys.exit(1)
    App().mainloop()
