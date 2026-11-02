"""CSE Compressor — 图形界面（Tkinter，多线程 + 进度条）。"""

import os
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from csengine import __version__, create_archive, extract_archive, list_archive
from csengine.compressors import METHOD_NAMES
from csengine.formats import human_size, ratio_percent

APP_TITLE = f"CSE Compressor v{__version__}"


class Worker:
    """后台任务线程，结果经 queue 回传 UI。"""

    def __init__(self, app):
        self.app = app

    def run(self, fn):
        def wrapper():
            try:
                fn()
                self.app.post(("done", None))
            except Exception as exc:  # 回传给 UI 线程处理
                self.app.post(("error", str(exc)))
        threading.Thread(target=wrapper, daemon=True).start()


class CompressorApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.q = queue.Queue()
        root.title(APP_TITLE)
        root.geometry("720x520")
        root.minsize(640, 460)
        self._build_ui()
        root.after(100, self._poll_queue)

    # ---------- UI 构建 ----------
    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}
        frm = ttk.Frame(self.root)
        frm.pack(fill="both", expand=True, **pad)

        # 源
        ttk.Label(frm, text="源文件/文件夹:").grid(row=0, column=0, sticky="w")
        self.var_src = tk.StringVar()
        ttk.Entry(frm, textvariable=self.var_src).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(frm, text="选择文件", command=self.pick_file).grid(row=0, column=2, padx=2)
        ttk.Button(frm, text="选择文件夹", command=self.pick_dir).grid(row=0, column=3, padx=2)

        # 输出
        ttk.Label(frm, text="输出路径:").grid(row=1, column=0, sticky="w")
        self.var_out = tk.StringVar()
        ttk.Entry(frm, textvariable=self.var_out).grid(row=1, column=1, sticky="ew", padx=4)
        ttk.Button(frm, text="浏览...", command=self.pick_out).grid(row=1, column=2, padx=2)

        # 方法
        ttk.Label(frm, text="压缩方式:").grid(row=2, column=0, sticky="w")
        self.var_method = tk.StringVar(value="auto")
        ttk.Combobox(frm, textvariable=self.var_method, state="readonly",
                     values=["auto", "store", "lzhuf", "zlib", "lzma"],
                     width=12).grid(row=2, column=1, sticky="w", padx=4)

        # 按钮
        btns = ttk.Frame(frm)
        btns.grid(row=3, column=0, columnspan=4, sticky="w", pady=6)
        ttk.Button(btns, text="开始压缩", command=self.start_compress).pack(side="left", padx=4)
        ttk.Button(btns, text="解压到...", command=self.start_extract).pack(side="left", padx=4)
        ttk.Button(btns, text="查看内容", command=self.start_list).pack(side="left", padx=4)

        # 进度
        self.progress = ttk.Progressbar(frm, mode="determinate")
        self.progress.grid(row=4, column=0, columnspan=4, sticky="ew", pady=4)
        self.var_status = tk.StringVar(value="就绪")
        ttk.Label(frm, textvariable=self.var_status, anchor="w").grid(
            row=5, column=0, columnspan=4, sticky="ew")

        # 日志
        logf = ttk.LabelFrame(frm, text="日志")
        logf.grid(row=6, column=0, columnspan=4, sticky="nsew", pady=6)
        self.log = tk.Text(logf, height=12, state="disabled", font=("Consolas", 9))
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        self.log.pack(side="left", fill="both", expand=True, padx=4, pady=4)
        sb.pack(side="right", fill="y")

        frm.columnconfigure(1, weight=1)
        frm.rowconfigure(6, weight=1)

    # ---------- 辅助 ----------
    def log_msg(self, msg: str):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def post(self, item):
        self.q.put(item)

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "progress":
                    done, total, name = payload
                    self.progress.configure(maximum=total, value=done)
                    self.var_status.set(f"[{done}/{total}] {name}")
                elif kind == "log":
                    self.log_msg(payload)
                elif kind == "done":
                    self.progress.configure(value=self.progress["maximum"])
                    self.var_status.set("完成")
                elif kind == "error":
                    self.var_status.set("出错")
                    messagebox.showerror(APP_TITLE, payload)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _require_src(self):
        src = self.var_src.get().strip()
        if not src or not os.path.exists(src):
            messagebox.showwarning(APP_TITLE, "请先选择有效的源文件或文件夹")
            return None
        return src

    def _run(self, fn):
        self.var_status.set("处理中...")
        Worker(self).run(fn)

    # ---------- 动作 ----------
    def pick_file(self):
        p = filedialog.askopenfilename(title="选择文件")
        if p:
            self.var_src.set(p)
            if not self.var_out.get():
                self.var_out.set(os.path.splitext(p)[0] + ".cse")

    def pick_dir(self):
        p = filedialog.askdirectory(title="选择文件夹")
        if p:
            self.var_src.set(p)
            if not self.var_out.get():
                self.var_out.set(p.rstrip("/\\") + ".cse")

    def pick_out(self):
        p = filedialog.asksaveasfilename(title="输出位置",
                                         defaultextension=".cse",
                                         filetypes=[("CSE 归档", "*.cse"), ("所有文件", "*.*")])
        if p:
            self.var_out.set(p)

    def start_compress(self):
        src = self._require_src()
        if not src:
            return
        out = self.var_out.get().strip()
        if not out:
            messagebox.showwarning(APP_TITLE, "请填写输出路径")
            return
        method = self.var_method.get()
        inputs = [src]

        def job():
            self.post(("log", f"压缩 {src} -> {out} (方法: {method})"))

            def prog(done, total, name):
                self.post(("progress", (done, total, name)))

            entries = create_archive(inputs, out, method=method, progress=prog)
            total_orig = sum(e.size for e in entries)
            total_comp = os.path.getsize(out)
            self.post(("log",
                       f"完成: {len(entries)} 个条目, {human_size(total_orig)} -> "
                       f"{human_size(total_comp)} ({ratio_percent(total_orig, total_comp)})"))
        self._run(job)

    def start_extract(self):
        src = self._require_src()
        if not src:
            return
        dest = filedialog.askdirectory(title="解压到哪个目录")
        if not dest:
            return

        def job():
            self.post(("log", f"解压 {src} -> {dest}"))

            def prog(done, total, name):
                self.post(("progress", (done, total, name)))

            files = extract_archive(src, dest, progress=prog)
            self.post(("log", f"完成: 解出 {len(files)} 个文件"))
        self._run(job)

    def start_list(self):
        src = self._require_src()
        if not src:
            return

        def job():
            self.post(("log", f"查看 {src}:"))
            self.post(("log", f"{'方式':<6} {'原始':>12} {'压缩':>12} {'比率':>8}  名称"))
            for e in list_archive(src):
                if e.is_dir:
                    self.post(("log", f"{'<目录>':<6} {'':>12} {'':>12} {'':>8}  {e.name}"))
                else:
                    self.post(("log",
                               f"{e.method:<6} {human_size(e.size):>12} "
                               f"{human_size(e.comp_size):>12} "
                               f"{ratio_percent(e.size, e.comp_size):>8}  {e.name}"))
        self._run(job)


def main():
    root = tk.Tk()
    try:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except Exception:
        pass
    CompressorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
