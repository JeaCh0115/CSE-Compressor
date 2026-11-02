"""控制台中文输出保底。

英文版 Windows 的控制台代码页是 cp1252，`print("压缩中 -> ...")` 会直接抛
UnicodeEncodeError，把整条命令带走——CI 上就是这么炸的：压缩还没开始，
进程已经因为"打印失败"退出了。真机是中文系统（cp936）所以一直没暴露。

做法：先把控制台代码页切到 UTF-8，再把标准流按 UTF-8 重配；
哪一步不被支持就退化成 errors="replace"——输出难看总比命令崩了强。
"""

import os
import sys


def setup_console() -> None:
    """让标准输出/错误流能安全打印中文。可重复调用，失败不抛异常。"""
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleOutputCP(65001)   # 仅对真实控制台有效，管道下无效也不影响
            kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        if stream is None:          # pythonw / 无控制台的打包形态
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            try:
                stream.reconfigure(errors="replace")
            except Exception:
                pass
