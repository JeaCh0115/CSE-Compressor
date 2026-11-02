"""CSE Compressor 统一启动器。

  python run.py app              现代化桌面应用（原生窗口 · 推荐）
  python run.py ui               浏览器模式（本地 Web 界面）
  python run.py gui              旧版 Tkinter 界面
  python run.py c 输入... -o x.cse [-f zip] [-m auto] [-p 密码]
  python run.py x x.cse -o 目录 [-p 密码]
  python run.py l x.cse          列出内容
  python run.py t x.cse          完整性测试
  python run.py info 文件         文件信息
  python run.py x.cse            用图形界面打开归档
"""

import os
import sys


SUBCOMMANDS = {"app", "desktop", "ui", "web", "gui",
               "c", "x", "l", "t", "info",
               "compress", "extract", "list", "test"}


def _bootstrap():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    # 仅当第一个参数就是归档文件（双击 .cse / run.py x.cse）时才进入图形界面。
    # 此前会在任意参数位查找 .cse 文件，导致 run.py l/t/x <归档> 被
    # 劫持去开 GUI——子进程因此挂起（流水线 CLI 冒烟超时的根因）。
    args = sys.argv[1:]
    if not args or args[0].startswith("-") or args[0] in SUBCOMMANDS:
        return None
    if os.path.isfile(args[0]):
        from csengine.converters import guess_format
        if guess_format(args[0]):
            from csgui.server import set_launch_file
            set_launch_file(os.path.abspath(args[0]))
            from csgui.desktop import main as desktop_main
            return desktop_main()
    return None


def main():
    _bootstrap()
    from cscli.console import setup_console
    setup_console()      # 英文系统控制台打印中文会直接崩，先把代码页/流处理好
    args = sys.argv[1:]
    if args and args[0] in ("app", "desktop"):
        from csgui.desktop import main as desktop_main
        port = 8770
        for a in args[1:]:
            if a.startswith("--port="):
                port = int(a.split("=", 1)[1])
        return desktop_main(port=port, debug="--debug" in args)
    if args and args[0] in ("ui", "web"):
        from csgui.server import main as ui_main
        port = 8770
        for a in args[1:]:
            if a.startswith("--port="):
                port = int(a.split("=", 1)[1])
        return ui_main(open_browser="--no-browser" not in args, port=port)
    if args and args[0] == "gui":
        from csgui.app import main as gui_main
        return gui_main()
    from cscli.main import main as cli_main
    return cli_main(args)


if __name__ == "__main__":
    sys.exit(main() or 0)
