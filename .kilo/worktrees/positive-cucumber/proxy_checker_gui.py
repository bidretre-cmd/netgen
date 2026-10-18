import queue
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    import requests
except ImportError:  # pragma: no cover - shown at runtime for users without deps.
    requests = None


DEFAULT_TEST_URL = "https://httpbin.org/ip"
USER_AGENT = "ProxyCheckerGUI/1.0"


@dataclass
class CheckResult:
    raw_proxy: str
    proxy_url: str
    status: str
    latency_ms: Optional[int]
    ip: str
    error: str


def normalize_proxy(raw_proxy: str, scheme: str) -> str:
    proxy = raw_proxy.strip()
    if "://" in proxy:
        return proxy
    return f"{scheme}://{proxy}"


def unique_proxy_entries(text: str) -> list[str]:
    seen: set[str] = set()
    proxies: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for item in re.split(r"[\s,;]+", stripped):
            item = item.strip()
            if not item or item.startswith("#"):
                continue
            if item not in seen:
                seen.add(item)
                proxies.append(item)
    return proxies


def extract_ip(response: "requests.Response") -> str:
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            data = response.json()
            if isinstance(data, dict):
                return str(data.get("origin") or data.get("ip") or "")
        except ValueError:
            pass
    return response.text.strip()[:120]


def check_proxy(raw_proxy: str, schemes: Iterable[str], test_url: str, timeout: float) -> CheckResult:
    if requests is None:
        return CheckResult(raw_proxy, "", "ERROR", None, "", "Install dependency: pip install -r requirements.txt")

    last_error = ""
    headers = {"User-Agent": USER_AGENT}
    proxy_candidates = [raw_proxy.strip()] if "://" in raw_proxy else [normalize_proxy(raw_proxy, scheme) for scheme in schemes]
    for proxy_url in proxy_candidates:
        proxies = {"http": proxy_url, "https": proxy_url}
        start = time.perf_counter()
        try:
            response = requests.get(
                test_url,
                proxies=proxies,
                timeout=timeout,
                headers=headers,
            )
            latency_ms = int((time.perf_counter() - start) * 1000)
            if 200 <= response.status_code < 400:
                return CheckResult(raw_proxy, proxy_url, "LIVE", latency_ms, extract_ip(response), "")
            last_error = f"HTTP {response.status_code}"
        except requests.RequestException as exc:
            last_error = str(exc).replace("\n", " ")[:180]
            if "Missing dependencies for SOCKS support" in last_error:
                last_error = "Install SOCKS support: pip install -r requirements.txt"

    fallback_proxy = proxy_candidates[0] if proxy_candidates else raw_proxy
    return CheckResult(raw_proxy, fallback_proxy, "DEAD", None, "", last_error)


class ProxyCheckerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Proxy Checker GUI")
        self.geometry("1040x700")
        self.minsize(860, 560)

        self.result_queue: queue.Queue[CheckResult | tuple[str, int, int]] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker_thread: Optional[threading.Thread] = None
        self.results: list[CheckResult] = []

        self.total_var = tk.StringVar(value="Total: 0")
        self.live_var = tk.StringVar(value="Live: 0")
        self.dead_var = tk.StringVar(value="Dead: 0")
        self.status_var = tk.StringVar(value="Ready")
        self.test_url_var = tk.StringVar(value=DEFAULT_TEST_URL)
        self.timeout_var = tk.StringVar(value="8")
        self.threads_var = tk.StringVar(value="50")
        self.http_var = tk.BooleanVar(value=True)
        self.https_var = tk.BooleanVar(value=True)
        self.socks4_var = tk.BooleanVar(value=False)
        self.socks5_var = tk.BooleanVar(value=True)

        self._configure_style()
        self._build_layout()
        self.after(100, self._drain_result_queue)

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("TButton", padding=(10, 6))
        style.configure("TLabel", padding=(2, 2))
        style.configure("Header.TLabel", font=("Segoe UI", 11, "bold"))
        style.configure("Live.TLabel", foreground="#117a37", font=("Segoe UI", 10, "bold"))
        style.configure("Dead.TLabel", foreground="#b3261e", font=("Segoe UI", 10, "bold"))

    def _build_layout(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        top = ttk.Frame(self, padding=12)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Test URL").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(top, textvariable=self.test_url_var).grid(row=0, column=1, sticky="ew", padx=(0, 12))

        ttk.Label(top, text="Timeout").grid(row=0, column=2, sticky="w")
        ttk.Spinbox(top, from_=1, to=60, textvariable=self.timeout_var, width=6).grid(row=0, column=3, padx=(6, 12))

        ttk.Label(top, text="Threads").grid(row=0, column=4, sticky="w")
        ttk.Spinbox(top, from_=1, to=300, textvariable=self.threads_var, width=6).grid(row=0, column=5, padx=(6, 0))

        middle = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        middle.grid(row=1, column=0, sticky="nsew", padx=12)

        left = ttk.Frame(middle)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)
        middle.add(left, weight=1)

        input_header = ttk.Frame(left)
        input_header.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        input_header.columnconfigure(0, weight=1)
        ttk.Label(input_header, text="Proxy List", style="Header.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(input_header, text="HTTP", variable=self.http_var).grid(row=0, column=1, padx=4)
        ttk.Checkbutton(input_header, text="HTTPS", variable=self.https_var).grid(row=0, column=2, padx=4)
        ttk.Checkbutton(input_header, text="SOCKS4", variable=self.socks4_var).grid(row=0, column=3, padx=4)
        ttk.Checkbutton(input_header, text="SOCKS5", variable=self.socks5_var).grid(row=0, column=4, padx=4)

        self.input_text = tk.Text(left, height=12, wrap="none", undo=True)
        self.input_text.grid(row=1, column=0, sticky="nsew")
        self.input_text.insert(
            "1.0",
            "# Format: host:port, user:pass@host:port, http://host:port, socks4://host:port, socks5://host:port\n",
        )
        input_scroll_y = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.input_text.yview)
        input_scroll_y.grid(row=1, column=1, sticky="ns")
        input_scroll_x = ttk.Scrollbar(left, orient=tk.HORIZONTAL, command=self.input_text.xview)
        input_scroll_x.grid(row=2, column=0, sticky="ew")
        self.input_text.configure(yscrollcommand=input_scroll_y.set, xscrollcommand=input_scroll_x.set)

        input_buttons = ttk.Frame(left)
        input_buttons.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(input_buttons, text="Load File", command=self.load_file).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(input_buttons, text="Clear", command=self.clear_input).pack(side=tk.LEFT)

        right = ttk.Frame(middle)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        middle.add(right, weight=2)

        result_header = ttk.Frame(right)
        result_header.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        result_header.columnconfigure(0, weight=1)
        ttk.Label(result_header, text="Results", style="Header.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(result_header, textvariable=self.total_var).grid(row=0, column=1, padx=8)
        ttk.Label(result_header, textvariable=self.live_var, style="Live.TLabel").grid(row=0, column=2, padx=8)
        ttk.Label(result_header, textvariable=self.dead_var, style="Dead.TLabel").grid(row=0, column=3, padx=8)

        columns = ("proxy", "status", "latency", "ip", "error")
        self.table = ttk.Treeview(right, columns=columns, show="headings", height=16)
        self.table.heading("proxy", text="Proxy")
        self.table.heading("status", text="Status")
        self.table.heading("latency", text="Latency")
        self.table.heading("ip", text="IP / Response")
        self.table.heading("error", text="Error")
        self.table.column("proxy", width=220, minwidth=160)
        self.table.column("status", width=70, minwidth=60, anchor=tk.CENTER)
        self.table.column("latency", width=80, minwidth=70, anchor=tk.CENTER)
        self.table.column("ip", width=160, minwidth=120)
        self.table.column("error", width=280, minwidth=180)
        self.table.grid(row=1, column=0, sticky="nsew")
        result_scroll_y = ttk.Scrollbar(right, orient=tk.VERTICAL, command=self.table.yview)
        result_scroll_y.grid(row=1, column=1, sticky="ns")
        result_scroll_x = ttk.Scrollbar(right, orient=tk.HORIZONTAL, command=self.table.xview)
        result_scroll_x.grid(row=2, column=0, sticky="ew")
        self.table.configure(yscrollcommand=result_scroll_y.set, xscrollcommand=result_scroll_x.set)
        self.table.tag_configure("live", foreground="#117a37")
        self.table.tag_configure("dead", foreground="#b3261e")
        self.table.tag_configure("error", foreground="#8a5a00")

        bottom = ttk.Frame(self, padding=12)
        bottom.grid(row=2, column=0, sticky="ew")
        bottom.columnconfigure(5, weight=1)
        self.start_button = ttk.Button(bottom, text="Start Check", command=self.start_check)
        self.start_button.grid(row=0, column=0, padx=(0, 8))
        self.stop_button = ttk.Button(bottom, text="Stop", command=self.stop_check, state=tk.DISABLED)
        self.stop_button.grid(row=0, column=1, padx=(0, 8))
        ttk.Button(bottom, text="Export Live", command=self.export_live).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(bottom, text="Copy Live", command=self.copy_live).grid(row=0, column=3, padx=(0, 8))
        ttk.Button(bottom, text="Clear Results", command=self.clear_results).grid(row=0, column=4, padx=(0, 12))
        self.progress = ttk.Progressbar(bottom, mode="determinate")
        self.progress.grid(row=0, column=5, sticky="ew", padx=(0, 12))
        ttk.Label(bottom, textvariable=self.status_var).grid(row=0, column=6, sticky="e")

    def load_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Load proxy list",
            filetypes=(("Text files", "*.txt"), ("All files", "*.*")),
        )
        if not path:
            return
        try:
            content = Path(path).read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            messagebox.showerror("Load failed", str(exc))
            return
        self.input_text.delete("1.0", tk.END)
        self.input_text.insert("1.0", content)
        self.status_var.set(f"Loaded: {Path(path).name}")

    def clear_input(self) -> None:
        self.input_text.delete("1.0", tk.END)

    def clear_results(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo("Still running", "Stop checker dulu sebelum clear results.")
            return
        self.results.clear()
        for item in self.table.get_children():
            self.table.delete(item)
        self.progress.configure(value=0, maximum=1)
        self._update_counters()
        self.status_var.set("Results cleared")

    def selected_schemes(self) -> list[str]:
        schemes = []
        if self.http_var.get():
            schemes.append("http")
        if self.https_var.get():
            schemes.append("https")
        if self.socks4_var.get():
            schemes.append("socks4")
        if self.socks5_var.get():
            schemes.append("socks5")
        return schemes

    def start_check(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            return
        if requests is None:
            messagebox.showerror("Missing dependency", "Install dulu: pip install -r requirements.txt")
            return

        proxies = unique_proxy_entries(self.input_text.get("1.0", tk.END))
        if not proxies:
            messagebox.showwarning("No proxies", "Masukkan daftar proxy terlebih dahulu.")
            return

        schemes = self.selected_schemes()
        if not schemes:
            messagebox.showwarning("No protocol", "Pilih minimal HTTP, HTTPS, SOCKS4, atau SOCKS5.")
            return

        try:
            timeout = max(1.0, float(self.timeout_var.get()))
            workers = max(1, min(300, int(self.threads_var.get())))
        except ValueError:
            messagebox.showerror("Invalid input", "Timeout dan Threads harus angka.")
            return

        self.clear_results()
        self.stop_event.clear()
        self.progress.configure(value=0, maximum=len(proxies))
        self.status_var.set("Checking...")
        self.start_button.configure(state=tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL)

        args = (proxies, schemes, self.test_url_var.get().strip() or DEFAULT_TEST_URL, timeout, workers)
        self.worker_thread = threading.Thread(target=self._run_checks, args=args, daemon=True)
        self.worker_thread.start()

    def stop_check(self) -> None:
        self.stop_event.set()
        self.status_var.set("Stopping...")
        self.stop_button.configure(state=tk.DISABLED)

    def _run_checks(
        self,
        proxies: list[str],
        schemes: list[str],
        test_url: str,
        timeout: float,
        workers: int,
    ) -> None:
        completed = 0
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(check_proxy, proxy, schemes, test_url, timeout): proxy for proxy in proxies}
            for future in as_completed(futures):
                if self.stop_event.is_set():
                    break
                completed += 1
                try:
                    result = future.result()
                except Exception as exc:  # Keeps the GUI alive if a worker fails unexpectedly.
                    proxy = futures[future]
                    result = CheckResult(proxy, proxy, "ERROR", None, "", str(exc)[:180])
                self.result_queue.put(result)
                self.result_queue.put(("progress", completed, len(proxies)))

            if self.stop_event.is_set():
                for future in futures:
                    future.cancel()
                self.result_queue.put(("done", completed, len(proxies)))
            else:
                self.result_queue.put(("done", len(proxies), len(proxies)))

    def _drain_result_queue(self) -> None:
        try:
            while True:
                item = self.result_queue.get_nowait()
                if isinstance(item, CheckResult):
                    self._add_result(item)
                elif item[0] == "progress":
                    _, done, total = item
                    self.progress.configure(value=done)
                    self.status_var.set(f"Checking {done}/{total}")
                elif item[0] == "done":
                    _, done, total = item
                    self.progress.configure(value=done)
                    self.start_button.configure(state=tk.NORMAL)
                    self.stop_button.configure(state=tk.DISABLED)
                    self.status_var.set(f"Done {done}/{total}" if done >= total else f"Stopped {done}/{total}")
        except queue.Empty:
            pass
        self.after(100, self._drain_result_queue)

    def _add_result(self, result: CheckResult) -> None:
        self.results.append(result)
        latency = f"{result.latency_ms} ms" if result.latency_ms is not None else "-"
        tag = result.status.lower()
        if tag not in {"live", "dead", "error"}:
            tag = ""
        self.table.insert(
            "",
            tk.END,
            values=(result.proxy_url or result.raw_proxy, result.status, latency, result.ip, result.error),
            tags=(tag,),
        )
        self._update_counters()

    def _update_counters(self) -> None:
        total = len(self.results)
        live = sum(1 for result in self.results if result.status == "LIVE")
        dead = sum(1 for result in self.results if result.status == "DEAD")
        self.total_var.set(f"Total: {total}")
        self.live_var.set(f"Live: {live}")
        self.dead_var.set(f"Dead: {dead}")

    def live_proxy_urls(self) -> list[str]:
        return [result.proxy_url for result in self.results if result.status == "LIVE" and result.proxy_url]

    def export_live(self) -> None:
        live = self.live_proxy_urls()
        if not live:
            messagebox.showinfo("No live proxies", "Belum ada proxy LIVE untuk diekspor.")
            return
        path = filedialog.asksaveasfilename(
            title="Save live proxies",
            defaultextension=".txt",
            filetypes=(("Text files", "*.txt"), ("All files", "*.*")),
        )
        if not path:
            return
        try:
            Path(path).write_text("\n".join(live) + "\n", encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("Export failed", str(exc))
            return
        self.status_var.set(f"Saved {len(live)} live proxies")

    def copy_live(self) -> None:
        live = self.live_proxy_urls()
        if not live:
            messagebox.showinfo("No live proxies", "Belum ada proxy LIVE untuk disalin.")
            return
        self.clipboard_clear()
        self.clipboard_append("\n".join(live))
        self.status_var.set(f"Copied {len(live)} live proxies")


def main() -> None:
    app = ProxyCheckerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
