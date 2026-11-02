"""PyInstaller 冻结态的 CLI 入口：cse-cli.exe（控制台程序）。"""

import multiprocessing
import os
import sys


def main():
    multiprocessing.freeze_support()
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from cscli.main import main as cli_main
    return cli_main()


if __name__ == "__main__":
    sys.exit(main() or 0)
