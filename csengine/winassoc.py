"""Windows 文件类型集成：ProgID / 图标 / 打开方式 / 右键菜单（仅 HKCU，无需管理员）。

供安装器与「工具 → 系统集成」共用；非 Windows 平台调用会抛 RuntimeError。
"""

import json
import os
import sys

try:
    import winreg
except ImportError:  # 非 Windows
    winreg = None

PROGID = "CSE.Compressor.Archive"
PROGID_DESC = "CSE-X 归档文件 (CSE Compressor)"
EXT = ".cse"

# 右键菜单：压缩（任意文件/文件夹）、解压（常见归档扩展名）
CTX_COMPRESS_KEY = r"Software\Classes\*\shell\CSE.Compress"
CTX_COMPRESS_DIR_KEY = r"Software\Classes\Directory\shell\CSE.Compress"
CTX_EXTRACT_EXTS = (".zip", ".7z", ".rar", ".tar", ".gz", ".tgz",
                    ".bz2", ".xz", ".iso", ".cab", ".lzh", ".arj")
# .cse 的解压动词随 ProgID 注册（register），这里不重复

# 压缩包扩展名 -> 统一设计的文件图标（右上角带格式徽标）
ARCHIVE_ICON_EXTS = {
    ".cse": "file-cse.ico",
    ".zip": "file-zip.ico",
    ".7z": "file-7z.ico",
    ".rar": "file-rar.ico",
    ".tar": "file-tar.ico",
    ".gz": "file-gz.ico",
    ".tgz": "file-gz.ico",
    ".tar.gz": "file-gz.ico",
    ".bz2": "file-bz2.ico",
    ".tbz2": "file-bz2.ico",
    ".tar.bz2": "file-bz2.ico",
    ".xz": "file-xz.ico",
    ".txz": "file-xz.ico",
    ".tar.xz": "file-xz.ico",
    ".iso": "file-iso.ico",
}
# 系统内置 ProgID：其 DefaultIcon 会盖过 SystemFileAssociations 的回退值，
# 想真正换掉 .zip 的图标必须单独覆盖（卸载时还原）
ZIP_PROGID = "CompressedFolder"
ZIP_ICON_BACKUP = "CSE.PreviousIcon"

# 图标覆盖记录：记住"我们到底改了谁的 DefaultIcon"，卸载时按记录精确还原。
# 值是 JSON 列表，元素形如 "progid:7-Zip.7z" / "sfa:.7z"。
ICON_TRACK_KEY = r"Software\CSECompressor\IconOverrides"
ICON_BACKUP_VALUE = "CSE.PreviousIcon"
USER_CHOICE_KEY = (r"Software\Microsoft\Windows\CurrentVersion"
                   r"\Explorer\FileExts\{ext}\UserChoice")

# ---- 默认打开方式 ----
# 每种压缩包扩展名给一个**独立 ProgID**（CSE.Archive.zip / CSE.Archive.7z …）。
# 好处：图标、双击打开、右键解压都归我们自己管，不必再去改 7-Zip 的 ProgID。
ARCHIVE_PROGID_PREFIX = "CSE.Archive"
OPEN_TRACK_KEY = r"Software\CSECompressor\OpenWith"
# 每种格式给人看的中文说明（写进 ProgID 默认值，资源管理器"类型"列显示它）
ARCHIVE_TYPE_LABELS = {
    ".cse": "CSE-X 归档文件",
    ".zip": "ZIP 压缩文件",
    ".7z": "7z 压缩文件",
    ".rar": "RAR 压缩文件",
    ".tar": "TAR 归档文件",
    ".gz": "GZIP 压缩文件",
    ".tgz": "TAR.GZ 压缩文件",
    ".bz2": "BZIP2 压缩文件",
    ".tbz2": "TAR.BZ2 压缩文件",
    ".xz": "XZ 压缩文件",
    ".txz": "TAR.XZ 压缩文件",
    ".iso": "光盘映像文件",
}


def _check():
    if winreg is None or os.name != "nt":
        raise RuntimeError("文件关联功能仅在 Windows 上可用")


def _exe() -> str:
    if getattr(sys, "frozen", False):
        return sys.executable
    raise RuntimeError("开发态请通过安装器注册文件关联")


def _icon_path() -> str:
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else ""
    if base:
        for rel in (os.path.join("_internal", "assets", "icons", "file-cse.ico"),
                    os.path.join("assets", "icons", "file-cse.ico")):
            p = os.path.join(base, rel)
            if os.path.isfile(p):
                return p
    return ""


def register(exe: str = None, icon: str = None) -> dict:
    """注册 .cse -> CSE.Compressor.Archive，并刷新图标缓存。

    exe/icon 可由安装器显式传入（指向安装目录里的 CSECompressor.exe 与 file-cse.ico）；
    缺省在冻结态取自身 exe，开发态不支持（请走安装器）。
    """
    _check()
    exe = exe or _exe()
    icon = icon or _icon_path() or exe
    open_cmd = f'"{exe}" "%1"'

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROGID}") as k:
        winreg.SetValueEx(k, None, 0, winreg.REG_SZ, PROGID_DESC)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROGID}\DefaultIcon") as k:
        winreg.SetValueEx(k, None, 0, winreg.REG_SZ, f'{icon},0')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROGID}\shell\open\command") as k:
        winreg.SetValueEx(k, None, 0, winreg.REG_SZ, open_cmd)
    # 资源管理器右键"用 CSE Compressor 解压"
    with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER,
            rf"Software\Classes\{PROGID}\shell\extract") as k:
        winreg.SetValueEx(k, None, 0, winreg.REG_SZ, "用 CSE Compressor 解压…")
        winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, f'{icon},0')
        # Single：只在单选一个归档时出现（多选时这条动词不该冒出来）
        winreg.SetValueEx(k, "MultiSelectModel", 0, winreg.REG_SZ, "Single")
    with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER,
            rf"Software\Classes\{PROGID}\shell\extract\command") as k:
        winreg.SetValueEx(k, None, 0, winreg.REG_SZ, f'"{exe}" --extract "%1"')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{EXT}") as k:
        winreg.SetValueEx(k, None, 0, winreg.REG_SZ, PROGID)
        winreg.SetValueEx(k, "Content Type", 0, winreg.REG_SZ, "application/x-cse-archive")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{EXT}\OpenWithProgids") as k:
        winreg.SetValueEx(k, PROGID, 0, winreg.REG_SZ, "")
    refresh_icons()
    return {"progid": PROGID, "exe": exe, "icon": icon}


def unregister() -> None:
    _check()
    for sub in (rf"{EXT}\OpenWithProgids", EXT,
                rf"{PROGID}\shell\extract\command", rf"{PROGID}\shell\extract",
                rf"{PROGID}\shell\open\command", rf"{PROGID}\shell",
                rf"{PROGID}\DefaultIcon", PROGID):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{sub}")
        except OSError:
            pass
    refresh_icons()


def is_registered() -> bool:
    _check()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{EXT}") as k:
            val, _ = winreg.QueryValueEx(k, None)
        return val == PROGID
    except OSError:
        return False


def register_context_menu(exe: str = None, icon: str = None) -> dict:
    """注册资源管理器右键菜单：

    - 文件 / 文件夹右键 → 「用 CSE Compressor 压缩…」（--add 带入压缩列表）
    - 常见归档右键     → 「用 CSE Compressor 解压…」（--extract 直接解压）
    仅写 HKCU，无需管理员。
    """
    _check()
    exe = exe or _exe()
    icon = icon or _icon_path() or exe

    def _verb(key_root: str, name: str, label: str, cmd: str, multi: str = "Player"):
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{key_root}\{name}") as k:
            winreg.SetValueEx(k, None, 0, winreg.REG_SZ, label)
            winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, f"{icon},0")
            # 命令行动词默认按 Document 模型处理，选中项超过 15 个就整条菜单消失；
            # Player 模型的额度是 100 项。多选压缩是主力场景，必须放宽。
            # 解压反过来：Single 模型下只在"单选"时出现——多选了几个文件、
            # 其中夹着一个压缩包时，不该冒出一条「解压」（%1 只会拿到其中一个）。
            winreg.SetValueEx(k, "MultiSelectModel", 0, winreg.REG_SZ, multi)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{key_root}\{name}\command") as k:
            winreg.SetValueEx(k, None, 0, winreg.REG_SZ, cmd)

    _verb(r"Software\Classes\*\shell", "CSE.Compress",
          "用 CSE Compressor 压缩…", f'"{exe}" --add "%1"')
    _verb(r"Software\Classes\Directory\shell", "CSE.Compress",
          "用 CSE Compressor 压缩…", f'"{exe}" --add "%1"')
    for ext in CTX_EXTRACT_EXTS:
        _verb(rf"Software\Classes\SystemFileAssociations\{ext}\shell", "CSE.Extract",
              "用 CSE Compressor 解压…", f'"{exe}" --extract "%1"', multi="Single")
    return {"exe": exe, "exts": list(CTX_EXTRACT_EXTS)}


def unregister_context_menu() -> None:
    _check()

    def _del(key_path: str):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
        except OSError:
            pass

    for verb_key in (CTX_COMPRESS_KEY, CTX_COMPRESS_DIR_KEY):
        _del(verb_key + r"\command")
        _del(verb_key)
    for ext in CTX_EXTRACT_EXTS:
        base = rf"Software\Classes\SystemFileAssociations\{ext}\shell\CSE.Extract"
        _del(base + r"\command")
        _del(base)


def is_context_menu_registered() -> bool:
    _check()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CTX_COMPRESS_KEY + r"\command") as k:
            winreg.QueryValueEx(k, None)
        return True
    except OSError:
        return False


# ---------------- 压缩包图标统一 ----------------
def _icon_dir() -> str:
    """图标文件夹：安装目录优先，开发态回落到仓库里的 assets/icons。"""
    if not getattr(sys, "frozen", False):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        p = os.path.join(here, "assets", "icons")
        return p if os.path.isdir(p) else ""
    base = os.path.dirname(sys.executable)
    for rel in ("assets/icons", os.path.join("_internal", "assets", "icons")):
        p = os.path.join(base, rel)
        if os.path.isdir(p):
            return p
    return ""


def register_archive_icons(icon_dir: str = None, exe: str = None) -> dict:
    r"""统一压缩包图标 **并接管双击打开**。

    资源管理器取图标的顺序是「扩展名关联到的 ProgID\DefaultIcon」优先，
    「SystemFileAssociations\<ext>\DefaultIcon」只是**回退**。装了 7-Zip / WinRAR
    的机器上 .7z/.rar/.zip 的 ProgID 分别是 7-Zip.*，所以只写回退键永远不生效。

    早期版本的做法是"借壳"：把我们的图标写进 7-Zip 的 ProgID\DefaultIcon。
    图标是换掉了，但双击仍然由 7-Zip 打开——用户看到的就是"图标是我们的、
    打开却是别人的程序"。现在改成**自立门户**：每种扩展名一个独立 ProgID
    （CSE.Archive.zip / CSE.Archive.7z …），图标、open 命令、解压动词都归自己，
    再把扩展名关联到它（HKCU 层，不需要管理员），并移除 UserChoice 这层
    强制覆盖（原值备份，卸载时写回）。
    """
    _check()
    icon_dir = icon_dir or _icon_dir()
    applied = {}
    if not icon_dir or not os.path.isdir(icon_dir):
        raise RuntimeError("找不到图标目录（开发态请通过安装器注册）")

    # 旧版本的"借壳"图标（改过 7-Zip.zip 之类第三方 ProgID）先撤回，
    # 否则用户换回 7-Zip 打开时，看到的还是我们的图标。
    _restore_progid_icons()

    tracked = _load_tracked()
    for ext, ico in ARCHIVE_ICON_EXTS.items():
        path = os.path.join(icon_dir, ico)
        if not os.path.isfile(path):
            continue
        # 回退键：图标层万一没生效（关联被系统重置）时还能兜一手
        key = rf"Software\Classes\SystemFileAssociations\{ext}\DefaultIcon"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
            winreg.SetValueEx(k, None, 0, winreg.REG_SZ, f"{path},0")
        tracked.add(f"sfa:{ext}")
        applied[ext] = {"icon": path}

    _save_tracked(tracked)

    # 自己的 ProgID + 扩展名关联（决定了"双击用谁打开"和"用哪个图标"）
    opened = None
    try:
        opened = register_archive_open(exe=exe, icon_dir=icon_dir, refresh=False)
    except RuntimeError:
        opened = None          # 开发态没有 exe：只做图标

    refresh_icons(hard=True)   # 立刻让资源管理器换图标，别让用户再点一次
    return {"icon_dir": icon_dir, "applied": applied,
            "count": len(applied), "open": opened}


# ---------------- 默认打开方式（每扩展名独立 ProgID） ----------------
def archive_progid(ext: str) -> str:
    """扩展名对应的 ProgID：CSE.Archive.zip / CSE.Archive.7z …"""
    return ARCHIVE_PROGID_PREFIX + ext


def openable_exts():
    """可以单独关联的压缩包扩展名（.tar.gz 这种双扩展名交给图标回退键）。"""
    return [e for e in ARCHIVE_ICON_EXTS if e != EXT and e.count(".") == 1]


def _write_archive_progid(pid: str, label: str, icon: str, exe: str) -> None:
    base = rf"Software\Classes\{pid}"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base) as k:
        winreg.SetValueEx(k, None, 0, winreg.REG_SZ, label)
        winreg.SetValueEx(k, "FriendlyTypeName", 0, winreg.REG_SZ, label)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r"\DefaultIcon") as k:
        winreg.SetValueEx(k, None, 0, winreg.REG_SZ, f"{icon},0")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r"\shell\open\command") as k:
        winreg.SetValueEx(k, None, 0, winreg.REG_SZ, f'"{exe}" "%1"')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r"\shell") as k:
        winreg.SetValueEx(k, None, 0, winreg.REG_SZ, "open")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r"\shell\extract") as k:
        winreg.SetValueEx(k, None, 0, winreg.REG_SZ, "用 CSE Compressor 解压…")
        winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, f"{icon},0")
        winreg.SetValueEx(k, "MultiSelectModel", 0, winreg.REG_SZ, "Single")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                          base + r"\shell\extract\command") as k:
        winreg.SetValueEx(k, None, 0, winreg.REG_SZ, f'"{exe}" --extract "%1"')
    # 出现在"打开方式 → 更多应用"里
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r"\OpenWithProgids") as k:
        winreg.SetValueEx(k, pid, 0, winreg.REG_SZ, "")


def _hkcu_default(path: str):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
            return winreg.QueryValueEx(k, None)[0]
    except OSError:
        return None


def _read_user_choice(ext: str):
    """读 UserChoice（Win8+ 的强制默认值，带 Hash 校验，只能删不能改）。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            USER_CHOICE_KEY.format(ext=ext)) as k:
            pid = winreg.QueryValueEx(k, "ProgId")[0]
            try:
                h = winreg.QueryValueEx(k, "Hash")[0]
            except OSError:
                h = ""
            return {"ProgId": pid, "Hash": h}
    except OSError:
        return None


def _drop_user_choice(ext: str) -> bool:
    """删掉 UserChoice 键。

    Win10/11 用 Hash 把用户选择的默认程序锁住，第三方程序**无权**写入；
    但删除键本身是允许的，删掉后系统会回落到 HKCU\\Software\\Classes\\<ext>
    的默认值——也就是我们刚写的 ProgID。原值记录在 OPEN_TRACK_KEY 里，
    卸载时连 Hash 一起写回，用户的原始选择不会丢。
    """
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER,
                         USER_CHOICE_KEY.format(ext=ext))
        return True
    except OSError:
        return False


def register_archive_open(exe: str = None, icon_dir: str = None,
                          refresh: bool = True) -> dict:
    """把常见压缩包扩展名关联到本程序（双击即用 CSE Compressor 打开）。"""
    _check()
    exe = exe or _exe()
    icon_dir = icon_dir or _icon_dir()
    rec = _load_open_track()
    done = {}
    for ext in openable_exts():
        ico = ARCHIVE_ICON_EXTS[ext]
        path = os.path.join(icon_dir, ico) if icon_dir else ""
        icon = path if path and os.path.isfile(path) else exe
        pid = archive_progid(ext)
        label = ARCHIVE_TYPE_LABELS.get(ext, ext.upper().lstrip(".") + " 压缩文件")
        _write_archive_progid(pid, label, icon, exe)

        entry = rec.get(ext) or {}
        # 只在第一次接管时记录原始状态，别把"我们自己的"写进备份
        if "hkcu" not in entry:
            prev = _hkcu_default(rf"Software\Classes\{ext}")
            if prev and prev != pid:
                entry["hkcu"] = prev
            else:
                entry["hkcu"] = None
        if "uc" not in entry:
            entry["uc"] = _read_user_choice(ext)
        rec[ext] = entry

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                              rf"Software\Classes\{ext}") as k:
            winreg.SetValueEx(k, None, 0, winreg.REG_SZ, pid)
            winreg.SetValueEx(k, "Content Type", 0, winreg.REG_SZ,
                              "application/x-cse-archive")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                              rf"Software\Classes\{ext}\OpenWithProgids") as k:
            winreg.SetValueEx(k, pid, 0, winreg.REG_SZ, "")
        _drop_user_choice(ext)
        done[ext] = pid

    _save_open_track(rec)
    if refresh:
        refresh_icons(hard=True)
    # 问一次外壳：这些扩展名**实际**会用哪个程序打开（注册表写成功不等于生效）
    own, pending = 0, []
    for ext in done:
        exe_now = resolve_open_exe(ext)
        if exe_now and os.path.basename(exe_now).lower() == os.path.basename(exe).lower():
            own += 1
        else:
            pending.append(ext)
    return {"progids": done, "count": len(done), "exe": exe,
            "own": own, "pending": pending}


def resolve_open_exe(ext: str) -> str:
    """让 Windows 自己回答「这个扩展名用哪个 exe 打开」。

    注册表写对了不等于生效：Windows 10/11 的默认应用保护会在某些类型上
    继续沿用旧的 UserChoice，只有问外壳才能知道真实结果。
    """
    try:
        import ctypes
        import ctypes.wintypes as wt

        shlwapi = ctypes.windll.shlwapi      # AssocQueryString 在 Shlwapi
        size = wt.DWORD(1024)
        buf = ctypes.create_unicode_buffer(1024)
        hr = shlwapi.AssocQueryStringW(0x00000004, 2, ext, "open", buf,
                                       ctypes.byref(size))   # ASSOCSTR_EXECUTABLE
        return buf.value if hr == 0 else ""
    except Exception:
        return ""


def unregister_archive_open() -> int:
    """还原默认打开方式：写回原关联与 UserChoice，删除自己建的 ProgID。"""
    _check()
    rec = _load_open_track()
    restored = 0
    for ext, entry in list(rec.items()):
        pid = archive_progid(ext)
        # 1) 扩展名默认值
        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                                  rf"Software\Classes\{ext}") as k:
                prev = entry.get("hkcu")
                if prev:
                    winreg.SetValueEx(k, None, 0, winreg.REG_SZ, prev)
                else:
                    try:
                        winreg.DeleteValue(k, None)
                    except OSError:
                        pass
        except OSError:
            pass
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER,
                             rf"Software\Classes\{ext}\OpenWithProgids")
        except OSError:
            pass
        # 2) UserChoice（连 Hash 一起写回，否则系统会认为被篡改）
        uc = entry.get("uc")
        if uc:
            try:
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                                      USER_CHOICE_KEY.format(ext=ext)) as k:
                    winreg.SetValueEx(k, "ProgId", 0, winreg.REG_SZ, uc["ProgId"])
                    if uc.get("Hash"):
                        winreg.SetValueEx(k, "Hash", 0, winreg.REG_SZ, uc["Hash"])
            except OSError:
                pass
        # 3) 删除自己建的 ProgID
        for sub in (r"shell\extract\command", r"shell\extract", r"shell\open\command",
                    r"shell\open", r"shell", r"OpenWithProgids", r"DefaultIcon", ""):
            key = rf"Software\Classes\{pid}" + (("\\" + sub) if sub else "")
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
            except OSError:
                pass
        rec.pop(ext, None)
        restored += 1
    _save_open_track(rec)
    return restored


def _load_open_track() -> dict:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, OPEN_TRACK_KEY) as k:
            raw, _ = winreg.QueryValueEx(k, "items")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_open_track(items: dict) -> None:
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, OPEN_TRACK_KEY) as k:
            if items:
                winreg.SetValueEx(k, "items", 0, winreg.REG_SZ,
                                  json.dumps(items, ensure_ascii=False))
            else:
                try:
                    winreg.DeleteValue(k, "items")
                except OSError:
                    pass
    except OSError:
        pass


def _restore_progid_icons() -> int:
    """撤回旧版本"借壳"写进第三方 ProgID 的图标。"""
    tracked = _load_tracked()
    n = 0
    for item in list(tracked):
        kind, _, name = item.partition(":")
        if kind != "progid":
            continue
        key = rf"Software\Classes\{name}\DefaultIcon"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0,
                                winreg.KEY_ALL_ACCESS) as k:
                try:
                    prev, _ = winreg.QueryValueEx(k, ICON_BACKUP_VALUE)
                except OSError:
                    prev = ""
                if prev:
                    winreg.SetValueEx(k, None, 0, winreg.REG_SZ, prev)
                else:
                    try:
                        winreg.DeleteValue(k, None)
                    except OSError:
                        pass
                try:
                    winreg.DeleteValue(k, ICON_BACKUP_VALUE)
                except OSError:
                    pass
            n += 1
        except OSError:
            pass
        tracked.discard(item)
    _save_tracked(tracked)
    return n



def unregister_archive_icons(icon_dir: str = None) -> int:
    """还原图标与默认打开方式。

    只认记录，不猜：早期版本没有记录，所以额外按**文件名**兜底清理回退键，
    避免"点了还原却没反应"。
    """
    _check()
    ours = {v.lower() for v in ARCHIVE_ICON_EXTS.values()}
    removed = 0
    tracked = _load_tracked()

    # 先还原"双击用谁打开"，再撤图标（顺序反过来会留下指向已删 ProgID 的关联）
    try:
        removed += unregister_archive_open()
    except Exception:
        pass

    for item in list(tracked):
        kind, _, name = item.partition(":")
        if kind == "sfa":
            key = rf"Software\Classes\SystemFileAssociations\{name}\DefaultIcon"
            if _ours_at(key, ours):
                try:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
                    removed += 1
                except OSError:
                    pass
            tracked.discard(item)
        elif kind == "progid":
            key = rf"Software\Classes\{name}\DefaultIcon"
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0,
                                    winreg.KEY_ALL_ACCESS) as k:
                    try:
                        prev, _ = winreg.QueryValueEx(k, ICON_BACKUP_VALUE)
                    except OSError:
                        prev = ""
                    if prev:
                        winreg.SetValueEx(k, None, 0, winreg.REG_SZ, prev)
                    else:
                        try:
                            winreg.DeleteValue(k, None)
                        except OSError:
                            pass
                    try:
                        winreg.DeleteValue(k, ICON_BACKUP_VALUE)
                    except OSError:
                        pass
                removed += 1
            except OSError:
                pass
            tracked.discard(item)

    # 兜底：旧版本（无记录）只写过 SystemFileAssociations
    for ext in ARCHIVE_ICON_EXTS:
        key = rf"Software\Classes\SystemFileAssociations\{ext}\DefaultIcon"
        if _ours_at(key, ours):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
                removed += 1
            except OSError:
                pass
        tracked.discard(f"sfa:{ext}")

    # 兜底：旧版本直接改过系统内置 CompressedFolder 的图标（带备份）
    legacy_key = rf"Software\Classes\{ZIP_PROGID}\DefaultIcon"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, legacy_key, 0,
                            winreg.KEY_ALL_ACCESS) as k:
            try:
                prev, _ = winreg.QueryValueEx(k, ZIP_ICON_BACKUP)
            except OSError:
                prev = ""
            if prev:
                winreg.SetValueEx(k, None, 0, winreg.REG_SZ, prev)
                winreg.DeleteValue(k, ZIP_ICON_BACKUP)
                removed += 1
    except OSError:
        pass

    _save_tracked(tracked)
    refresh_icons(hard=True)
    return removed


def is_archive_icons_registered() -> bool:
    r"""已统一 = 至少一种压缩包的**关联**指向我们的 ProgID（图标与打开方式都生效）。

    旧版本只做过图标替换（没有 open 记录），保留原来的判定作为兜底。
    """
    _check()
    if _load_open_track():
        return any(effective_progid(ext) == archive_progid(ext)
                   for ext in openable_exts())
    if not _load_tracked():
        return False
    for ext in (".7z", ".zip"):
        progid = effective_progid(ext)
        val = ""
        if progid:
            val = _read_hkcr(rf"{progid}\DefaultIcon") or ""
        if not val or "file-" not in val.lower():
            val = _read_hkcr(rf"SystemFileAssociations\{ext}\DefaultIcon") or ""
        if "file-" in val.lower():
            return True
    return False


# ---------------- 图标覆盖的底层工具 ----------------
def _read_hkcr(path: str):
    """读 HKCR 合并视图里的默认值（HKCU 优先于 HKLM）。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, path) as k:
            return winreg.QueryValueEx(k, None)[0]
    except OSError:
        return None


def effective_progid(ext: str):
    """扩展名实际生效的 ProgID：UserChoice > HKCU 关联 > HKLM 关联。"""
    _check()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            USER_CHOICE_KEY.format(ext=ext)) as k:
            val, _ = winreg.QueryValueEx(k, "ProgId")
        if val:
            return val
    except OSError:
        pass
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, rf"Software\Classes\{ext}") as k:
                val, _ = winreg.QueryValueEx(k, None)
            if val:
                return val
        except OSError:
            continue
    return ""


def _ours_at(key: str, ours: set) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            cur, _ = winreg.QueryValueEx(k, None)
    except OSError:
        return False
    return os.path.basename(str(cur).split(",")[0].strip().lower()) in ours


def _load_tracked() -> set:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, ICON_TRACK_KEY) as k:
            raw, _ = winreg.QueryValueEx(k, "items")
        return set(json.loads(raw))
    except (OSError, ValueError):
        return set()


def _save_tracked(items: set) -> None:
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, ICON_TRACK_KEY) as k:
            if items:
                winreg.SetValueEx(k, "items", 0, winreg.REG_SZ,
                                  json.dumps(sorted(items)))
            else:
                try:
                    winreg.DeleteValue(k, "items")
                except OSError:
                    pass
    except OSError:
        pass


def refresh_icons(hard: bool = False) -> None:
    """通知资源管理器刷新图标缓存。

    hard=True 时额外跑一次 `ie4uinit.exe -show`：SHChangeNotify 只让外壳
    重新读关联，已缓存过的单个文件图标（Win11 的逐项图标缓存）有时仍不换，
    这一步会重建图标缓存。
    """
    _check()
    import ctypes
    SHCNE_ASSOCCHANGED = 0x08000000
    SHCNF_IDLIST = 0x0000
    SHCNF_FLUSH = 0x1000
    try:
        ctypes.windll.shell32.SHChangeNotify(
            SHCNE_ASSOCCHANGED, SHCNF_IDLIST | SHCNF_FLUSH, None, None)
    except Exception:
        pass
    if not hard:
        return
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                       "System32", "ie4uinit.exe")
    if os.path.isfile(exe):
        try:
            import subprocess
            subprocess.run([exe, "-show"], timeout=8,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
