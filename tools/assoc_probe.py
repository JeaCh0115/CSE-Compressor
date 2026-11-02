"""文件关联实测探针：注册 → 问 Windows "谁打开这个 .zip" → 还原。

用法：
  python tools/assoc_probe.py [--keep]
  --keep  测完不还原（便于在资源管理器里肉眼复核）

判定不靠读注册表自说自话，而是调 AssocQueryStringW 让外壳自己回答
「.zip 的 open 命令是什么 / 图标是什么」。
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from csengine import winassoc as W  # noqa: E402

EXE = r"C:\Program Files\CSE Compressor\CSECompressor.exe"
ICONS = r"C:\Program Files\CSE Compressor\assets\icons"
EXTS = (".zip", ".7z", ".rar", ".iso", ".tar", ".gz", ".xz", ".bz2")


def assoc_string(ext, verb="open"):
    """让 shell 回答：这个扩展名的 open 命令 / 可执行文件。"""
    shlwapi = ctypes.windll.shlwapi   # AssocQueryString 在 Shlwapi，不在 shell32
    ASSOCSTR_EXECUTABLE, ASSOCSTR_COMMAND = 2, 1
    out = {}
    for name, flag in (("exe", ASSOCSTR_EXECUTABLE), ("cmd", ASSOCSTR_COMMAND)):
        n = wt.DWORD(1024)
        buf = ctypes.create_unicode_buffer(1024)
        hr = shlwapi.AssocQueryStringW(0x00000004,  # ASSOCF_INIT_IGNOREUNKNOWN
                                       flag, ext, verb, buf, ctypes.byref(n))
        out[name] = buf.value if hr == 0 else f"<err 0x{hr & 0xFFFFFFFF:08x}>"
    return out


def main():
    keep = "--keep" in sys.argv
    if not os.path.isfile(EXE):
        raise SystemExit(f"找不到 {EXE}，请先安装")
    print("== 注册前")
    for ext in EXTS:
        print(f"  {ext:6s} {W.effective_progid(ext):24s} "
              f"{assoc_string(ext)['exe']}")

    res = W.register_archive_open(exe=EXE, icon_dir=ICONS)
    print(f"\n== 已注册 {res['count']} 种扩展名，其中 {res['own']} 种已生效")
    bad = []
    for ext in EXTS:
        progid = W.effective_progid(ext)
        info = assoc_string(ext)
        ok = progid == W.archive_progid(ext) and EXE.lower() in info["exe"].lower()
        print(f"  {'OK ' if ok else 'BAD'} {ext:6s} {progid:22s} {info['exe']}")
        if not ok:
            bad.append(ext)
    if res.get("pending"):
        print(f"  系统仍按旧程序打开: {res['pending']}（Windows 默认应用保护，需用户在设置里确认一次）")

    if not keep:
        n = W.unregister_archive_open()
        print(f"\n== 已还原 {n} 项")
        for ext in EXTS:
            print(f"  {ext:6s} {W.effective_progid(ext):24s} "
                  f"{assoc_string(ext)['exe']}")
    print("\n结论:", "全部生效" if not bad else f"未生效: {bad}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
