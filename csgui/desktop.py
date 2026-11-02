"""CSE Compressor 桌面外壳（Windows）。

用 pywebview + Edge WebView2 把本地 Web 界面包成原生窗口：
- 独立任务栏图标（AppUserModelID + 应用 .ico），点击任务栏图标可最小化/还原
- 无边框 + 自绘标题栏；补 WS_MINIMIZEBOX 让「点任务栏图标最小化」生效，
  并用 WM_NCHITTEST 子类化实现四边/四角鼠标拉伸
- 进程 DPI 感知（Per-Monitor V2），高分屏不模糊
- 单实例：重复启动会把已有窗口唤起到前台，而不是再开一个
- 启动即显：窗口先加载内置启动页，后台线程起服务后再载入正式界面
- 记忆窗口尺寸/位置/最大化状态（settings.json）
"""

import ctypes
import ctypes.wintypes as wt
import os
import sys
import threading
import time

from . import _trace as trace
from . import runtime
from . import settings

MUTEX_NAME = runtime.MUTEX_NAME
DEFAULT_SIZE = (860, 500)
MIN_SIZE = (720, 430)
COMPACT_SIZE = (560, 560)   # 右键「压缩」只弹压缩对话框时的小窗（一页放完所有设置，按钮贴底）
COMPACT_EXTRACT_SIZE = (540, 420)   # 右键「解压」：设置面板（选解压位置）+ 进度/结果
COMPACT_MIN = (480, 300)
RESIZE_MARGIN = 6          # 边缘拉伸热区（像素）
GEOMETRY_VERSION = 3       # 记忆格式版本：旧版本记忆作废，回落到 860×500

# 紧凑模式开关：窗口当前是不是"只显示一个面板"的小窗。
# _save_geometry 要据此跳过记忆，否则用户下次开软件会得到 560×680 的小窗。
COMPACT = {"on": False, "kind": "", "size": COMPACT_SIZE}

byref = ctypes.byref
user32 = ctypes.windll.user32

# 窗口样式位
GWL_STYLE = -16
WS_SYSMENU = 0x00080000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WS_THICKFRAME = 0x00040000

WM_NCHITTEST = 0x0084
WM_NCLBUTTONDOWN = 0x00A1
WM_SYSCOMMAND = 0x0112
SC_SIZE = 0xF000
HT_CLIENT = 1
HT_LEFT, HT_RIGHT, HT_TOP, HT_TOPLEFT = 10, 11, 12, 13
HT_TOPRIGHT, HT_BOTTOM, HT_BOTTOMLEFT, HT_BOTTOMRIGHT = 14, 15, 16, 17
_RESIZE_HITS = (HT_LEFT, HT_RIGHT, HT_TOP, HT_TOPLEFT,
                HT_TOPRIGHT, HT_BOTTOM, HT_BOTTOMLEFT, HT_BOTTOMRIGHT)


# ---------------- 启动页（窗口立即出现，服务就绪后替换） ----------------
# 用 __X__ 占位 + str.replace 注入配色：CSS 里 % 太多，用 %-格式化极易踩坑
# 56px 应用图标（内联 base64：启动页走 load_html，引用不到 /static 资源）
_SPLASH_LOGO = (
    "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"
)

SPLASH_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><title>CSE Compressor</title>
<style>
  html,body{height:100%;margin:0}
  body{display:grid;place-items:center;font-family:"Segoe UI","Microsoft YaHei",sans-serif;
       background:__BG__;color:__FG__;-webkit-user-select:none}
  .box{display:flex;flex-direction:column;align-items:center;gap:14px}
  .logo{width:56px;height:56px;display:grid;place-items:center}
  .logo img{width:56px;height:56px;display:block}
  .name{font-size:14px;font-weight:600;letter-spacing:.4px}
  .tip{font-size:12px;color:__MUTED__}
  .bar{width:132px;height:3px;border-radius:2px;background:__TRACK__;overflow:hidden}
  .bar i{display:block;height:100%;width:40%;border-radius:2px;
         background:linear-gradient(90deg,#6676ff,#3a2fb5);animation:run 1.1s ease-in-out infinite}
  @keyframes run{0%{transform:translateX(-100%)}100%{transform:translateX(330%)}}
</style></head>
<body><div class="box">
  <div class="logo"><img src="data:image/png;base64,__LOGO__" alt=""></div>
  <div class="name">CSE Compressor</div>
  <div class="bar"><i></i></div>
  <div class="tip">正在载入…</div>
</div></body></html>"""


def _splash_html() -> str:
    try:
        theme = settings.load().get("theme") or "dark"
    except Exception:
        theme = "dark"
    dark = _resolve_dark(theme)
    tokens = {
        "__BG__": "#0b1020" if dark else "#f4f6fb",
        "__FG__": "#eef2ff" if dark else "#0f172a",
        "__MUTED__": "#6c7899" if dark else "#8592ad",
        "__TRACK__": "rgba(255,255,255,.10)" if dark else "rgba(16,24,40,.10)",
        "__LOGO__": _SPLASH_LOGO,
    }
    html = SPLASH_HTML
    for k, v in tokens.items():
        html = html.replace(k, v)
    return html


def _splash_bg() -> str:
    """窗口背景色（= 启动页底色）。

    WinForms 建窗时会先铺这层纯色，WebView2 渲染出内容后才盖住它。
    以前固定深色 + 建窗即显示，开机瞬间就是一块「全黑」；现在两边同色，
    再配合「渲染完成才显示」，就看不到那一下黑了。
    """
    try:
        theme = settings.load().get("theme") or "dark"
    except Exception:
        theme = "dark"
    return "#0b1020" if _resolve_dark(theme) else "#f4f6fb"



# ---------------- Windows 环境准备 ----------------
def _dpi_aware():
    try:
        PROCESS_PER_MONITOR_DPI_AWARE_V2 = ctypes.c_void_p(-4)
        ctypes.windll.user32.SetProcessDpiAwarenessContext(PROCESS_PER_MONITOR_DPI_AWARE_V2)
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_DPI_AWARE
        except Exception:
            pass


def _app_user_model_id():
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CSE.Project.Compressor")
    except Exception:
        pass


def _icon_path() -> str:
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for rel in (os.path.join("assets", "icons", "cse-app.ico"),
                os.path.join("_internal", "assets", "icons", "cse-app.ico")):
        p = os.path.join(base, rel)
        if os.path.isfile(p):
            return p
    return ""


# ---------------- 单实例 ----------------
def _find_existing_window():
    """找到已有主窗口句柄（无则返回 0，可容忍残留的僵尸进程）。"""
    found = []
    user32 = ctypes.windll.user32

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        if buf.value.startswith("CSE Compressor") and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else 0


def _activate_existing():
    hwnd = _find_existing_window()
    if not hwnd:
        return False
    user32 = ctypes.windll.user32
    user32.ShowWindow(hwnd, 9)          # SW_RESTORE：从最小化还原
    user32.SetForegroundWindow(hwnd)
    return True


def _single_instance():
    """返回 True 表示本进程应当开窗。命名互斥锁随进程退出自动释放。

    入口（app_entry）可能**已经**选过举：多选文件右键时它需要在 N 个并发
    进程里挑一个当主实例，锁就握在它手里。此时这里再 CreateMutex 会拿到
    ERROR_ALREADY_EXISTS（哪怕锁是本进程自己的），必须跳过，否则会误判
    "已有实例"→ 找不到窗口 → 直接退出，表现为右键压缩没反应。
    """
    if os.environ.get("CSE_SINGLETON_OWNER") == "1":
        return True
    from . import runtime
    if not runtime.acquire_singleton(MUTEX_NAME):
        if _activate_existing():
            return False
        # 已有互斥锁但找不到窗口（僵尸进程）→ 照常启动
    return True


# ---------------- 原生窗口控制 ----------------
class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [("length", ctypes.c_uint), ("flags", ctypes.c_uint),
                ("showCmd", ctypes.c_uint), ("ptMinPosition", wt.POINT),
                ("ptMaxPosition", wt.POINT), ("rcNormalPosition", wt.RECT)]


def _window_show_cmd(hwnd) -> int:
    """读取窗口真实显示状态：1=正常 2=最小化 3=最大化。

    不能用 WinForms WindowState —— 通过 user32.ShowWindow 直接最大化时
    该属性不会同步，用它判断会导致"最大化后永远无法还原"。
    """
    try:
        wp = WINDOWPLACEMENT()
        wp.length = ctypes.sizeof(WINDOWPLACEMENT)
        if ctypes.windll.user32.GetWindowPlacement(hwnd, byref(wp)):
            return wp.showCmd
    except Exception:
        pass
    return 1


def _set_titlebar_dark(window, dark: bool):
    """让标题栏配色跟随主题（Win10 1809+ / Win11）。"""
    try:
        hwnd = int(window.native.Handle.ToInt64())
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE 新旧取值
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, byref(ctypes.c_int(int(dark))), 4) == 0:
                break
    except Exception:
        pass


# ---------------- 无边框窗口：任务栏最小化 + 边缘拉伸 ----------------
def _enable_frame_behaviour(window) -> int:
    """补上系统样式位，让「点任务栏图标最小化」按原生行为生效。

    无边框窗口（FormBorderStyle.None）会丢掉 WS_MINIMIZEBOX，
    而任务栏按钮的"最小化当前窗口"行为依赖该样式位，缺了就只能激活不能最小化。
    """
    try:
        hwnd = int(window.native.Handle.ToInt64())
        style = user32.GetWindowLongW(hwnd, GWL_STYLE)
        user32.SetWindowLongW(hwnd, GWL_STYLE,
                              style | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU)
        try:  # 最大化时留出任务栏（无边框窗口默认会盖住）
            from System.Windows.Forms import Screen
            window.native.MaximizedBounds = Screen.FromHandle(
                window.native.Handle).WorkingArea
        except Exception:
            pass
        return hwnd
    except Exception:
        return 0


SWP_NOSIZE, SWP_NOMOVE = 0x0001, 0x0002
SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
SW_SHOWNOACTIVATE = 4


def _set_outer_size(hwnd, w, h) -> bool:
    """把窗口的**外框**尺寸校正到指定像素。

    pywebview/WinForms 建窗时按客户区换算尺寸，无边框窗口会少掉几个像素
    （实测 860×500 请求 → 844×461 实际），这里直接校正回请求值。
    """
    if not hwnd or w <= 0 or h <= 0:
        return False
    try:
        r = wt.RECT()
        if not user32.GetWindowRect(hwnd, byref(r)):
            return False
        if (r.right - r.left, r.bottom - r.top) == (w, h):
            return True
        user32.SetWindowPos(hwnd, 0, r.left, r.top, int(w), int(h),
                            SWP_NOZORDER | SWP_NOACTIVATE)
        return True
    except Exception:
        return False


_SUBCLASS_ID = 0xC5E01
_sub_proc = None
_def_subclass = None
_prev_proc = None
GWLP_WNDPROC = -4


def _resize_centered(hwnd, w, h) -> bool:
    """改窗口尺寸并居中（保持在当前显示器的工作区内）。"""
    if not hwnd or w <= 0 or h <= 0:
        return False
    try:
        MONITOR_DEFAULTTONEAREST = 2

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT),
                        ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]

        mon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if mon and user32.GetMonitorInfoW(mon, byref(mi)):
            wa = mi.rcWork
        else:
            wa = wt.RECT(0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))
        x = wa.left + max(0, (wa.right - wa.left - w) // 2)
        y = wa.top + max(0, (wa.bottom - wa.top - h) // 2)
        user32.SetWindowPos(hwnd, 0, int(x), int(y), int(w), int(h),
                            SWP_NOZORDER | SWP_NOACTIVATE)
        return True
    except Exception:
        return False

def _hwnd_of(window) -> int:
    try:
        return int(window.native.Handle.ToInt64())
    except Exception:
        return 0


def _offscreen_pos(w, h):
    """虚拟桌面右侧之外的坐标：窗口在那里显示，用户看不见。

    紧凑小窗要走"先热身、再上台"：WebView2 出画要 200~400ms，如果直接显示在
    屏幕中央，用户会先看到一块纯底色（"什么都没有"）再看到面板。放到屏幕外
    等它画好再挪进来，第一眼就是完整界面。多显示器时按虚拟屏右边界算。
    """
    SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
    SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
    try:
        vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN) or 1920
        vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN) or 1080
    except Exception:
        vx, vy, vw, vh = 0, 0, 1920, 1080
    return (int(vx + vw + 80), int(vy + max(0, (vh - h) // 2)))


def _painted(hwnd, w=None, h=None) -> bool:
    """窗口内容画出来了吗？

    判据：PrintWindow 抓窗口中心一小块，颜色种类 > 3 就算画好——纯底色只有
    1~2 种颜色。比"loaded 事件"准，loaded 只代表页面载入完成，WebView2 还要
    再花一两百毫秒才真正出像素。
    """
    if not hwnd:
        return False
    try:
        import ctypes.wintypes as _wt

        r = _wt.RECT()
        if not user32.GetWindowRect(hwnd, byref(r)):
            return False
        ww, hh = r.right - r.left, r.bottom - r.top
        if ww <= 0 or hh <= 0:
            return False
        sw, sh = 60, 40
        sx = r.left + max(0, (ww - sw) // 2)
        sy = r.top + max(0, (hh - sh) // 2)
        hdc_screen = user32.GetDC(0)
        hdc = ctypes.windll.gdi32.CreateCompatibleDC(hdc_screen)
        bmp = ctypes.windll.gdi32.CreateCompatibleBitmap(hdc_screen, sw, sh)
        ctypes.windll.gdi32.SelectObject(hdc, bmp)
        PW_RENDERFULLCONTENT = 0x00000002
        ok = user32.PrintWindow(hwnd, hdc, PW_RENDERFULLCONTENT)

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG),
                        ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                        ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                        ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                        ("biClrImportant", wt.DWORD)]

        bi = BITMAPINFOHEADER()
        bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.biWidth, bi.biHeight = sw, -sh
        bi.biPlanes, bi.biBitCount = 1, 32
        buf = ctypes.create_string_buffer(sw * sh * 4)
        got = ctypes.windll.gdi32.GetDIBits(hdc, bmp, 0, sh, buf,
                                            byref(bi), 0)
        ctypes.windll.gdi32.DeleteObject(bmp)
        ctypes.windll.gdi32.DeleteDC(hdc)
        user32.ReleaseDC(0, hdc_screen)
        if not ok or not got:
            return False
        colors = {buf.raw[i:i + 4] for i in range(0, len(buf.raw), 4)}
        return len(colors) > 3
    except Exception:
        return False


def _move_centered(hwnd, w, h, activate=True) -> bool:
    """把窗口移到当前显示器工作区中央（可按需激活）。"""
    if not hwnd or w <= 0 or h <= 0:
        return False
    try:
        MONITOR_DEFAULTTONEAREST = 2

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT),
                        ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]

        mon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if mon and user32.GetMonitorInfoW(mon, byref(mi)):
            wa = mi.rcWork
        else:
            wa = wt.RECT(0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))
        x = wa.left + max(0, (wa.right - wa.left - w) // 2)
        y = wa.top + max(0, (wa.bottom - wa.top - h) // 2)
        flags = SWP_NOZORDER | (0 if activate else SWP_NOACTIVATE)
        user32.SetWindowPos(hwnd, 0, int(x), int(y), int(w), int(h), flags)
        if activate:
            try:
                user32.SetForegroundWindow(hwnd)
            except Exception:
                pass
        return True
    except Exception:
        return False


def _hit_test(hwnd, lparam, margin=RESIZE_MARGIN) -> int:
    """四边 / 四角返回对应的 HT*，其余返回 0 交给默认处理。"""
    try:
        if _window_show_cmd(hwnd) == 3:  # 最大化状态不做边缘拉伸
            return 0
        x = ctypes.c_short(lparam & 0xFFFF).value
        y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
        r = wt.RECT()
        if not user32.GetWindowRect(hwnd, byref(r)):
            return 0
        left = abs(x - r.left) <= margin
        right = abs(r.right - x) <= margin
        top = abs(y - r.top) <= margin
        bottom = abs(r.bottom - y) <= margin
        if top and left:
            return HT_TOPLEFT
        if top and right:
            return HT_TOPRIGHT
        if bottom and left:
            return HT_BOTTOMLEFT
        if bottom and right:
            return HT_BOTTOMRIGHT
        if left:
            return HT_LEFT
        if right:
            return HT_RIGHT
        if top:
            return HT_TOP
        if bottom:
            return HT_BOTTOM
    except Exception:
        pass
    return 0


WM_MOUSEMOVE = 0x0200
WM_LBUTTONUP = 0x0202
WM_CAPTURECHANGED = 0x0215

# 自控缩放循环状态：无边框窗口上 DefWindowProc 不接管 SC_SIZE，只能自己算尺寸
_DRAG = {"on": False, "edge": 0, "sx": 0, "sy": 0, "box": None,
         "hwnd": 0, "min": (0, 0)}


def _drag_begin(hwnd, edge, sx=None, sy=None) -> bool:
    """边缘按下：抓鼠标、记下起点与窗口矩形，之后由 WM_MOUSEMOVE 推尺寸。

    sx/sy 由前端传入按下瞬间的屏幕坐标（物理像素），避免 JS 到 Python 的
    往返延迟让起点漂移、窗口一按就跳。
    """
    try:
        pt = wt.POINT()
        user32.GetCursorPos(byref(pt))
        r = wt.RECT()
        user32.GetWindowRect(hwnd, byref(r))
        try:
            user32.GetDpiForWindow.argtypes = [wt.HWND]
            user32.GetDpiForWindow.restype = ctypes.c_uint
            scale = (user32.GetDpiForWindow(hwnd) or 96) / 96.0
        except Exception:
            scale = 1.0
        # 前端传来的坐标只作起点参考：换算偏差过大就退回真实光标位置
        px = int(sx) if sx else 0
        py = int(sy) if sy else 0
        if not px or abs(px - pt.x) > 240:
            px = pt.x
        if not py or abs(py - pt.y) > 240:
            py = pt.y
        _DRAG.update(on=True, edge=edge, sx=px, sy=py,
                     box=[r.left, r.top, r.right, r.bottom], hwnd=hwnd,
                     min=(int(MIN_SIZE[0] * scale), int(MIN_SIZE[1] * scale)))
        user32.SetCapture(hwnd)
        return True
    except Exception:
        return False


def _drag_move() -> None:
    """按鼠标位移实时改窗口矩形（拖左/上边时原点反向移动）。"""
    if not _DRAG["on"]:
        return
    try:
        if user32.GetCapture() != _DRAG["hwnd"]:
            _DRAG["on"] = False
            return
        pt = wt.POINT()
        user32.GetCursorPos(byref(pt))
        dx, dy = pt.x - _DRAG["sx"], pt.y - _DRAG["sy"]
        l, t, r, b = _DRAG["box"]
        e = _DRAG["edge"]
        if e in (HT_LEFT, HT_TOPLEFT, HT_BOTTOMLEFT):
            l += dx
        elif e in (HT_RIGHT, HT_TOPRIGHT, HT_BOTTOMRIGHT):
            r += dx
        if e in (HT_TOP, HT_TOPLEFT, HT_TOPRIGHT):
            t += dy
        elif e in (HT_BOTTOM, HT_BOTTOMLEFT, HT_BOTTOMRIGHT):
            b += dy
        mw, mh = _DRAG["min"]
        w, h = max(mw, r - l), max(mh, b - t)
        if e in (HT_LEFT, HT_TOPLEFT, HT_BOTTOMLEFT):
            l = r - w
        else:
            r = l + w
        if e in (HT_TOP, HT_TOPLEFT, HT_TOPRIGHT):
            t = b - h
        else:
            b = t + h
        user32.SetWindowPos(_DRAG["hwnd"], 0, int(l), int(t), int(w), int(h),
                            SWP_NOZORDER | SWP_NOACTIVATE)
    except Exception:
        _DRAG["on"] = False


def _drag_end() -> None:
    if _DRAG["on"]:
        _DRAG["on"] = False
        try:
            user32.ReleaseCapture()
        except Exception:
            pass


def _resize_msg(hwnd, msg, wparam, lparam, parent):
    """拉伸相关消息的统一处理；返回 None 表示交回原来的窗口过程。"""
    if msg == WM_NCHITTEST:
        hit = _hit_test(parent, lparam)
        return hit if hit else None
    if msg == WM_NCLBUTTONDOWN and wparam in _RESIZE_HITS:
        _drag_begin(parent, wparam)
        return 0
    if msg == WM_MOUSEMOVE:
        _drag_move()
        return None
    if msg in (WM_LBUTTONUP, WM_CAPTURECHANGED):
        _drag_end()
        return None
    return None


def _install_resize(hwnd) -> bool:
    """子类化窗口过程，实现无边框窗口的鼠标拉伸（原生手感，非 JS 模拟）。

    两条路径：优先 comctl32 的 SetWindowSubclass（干净、可安全移除）；
    但系统里未激活 comctl32 v6 时该 API 不存在（实测返回失败），
    此时退化为 SetWindowLongPtr(GWLP_WNDPROC) 换过程 + CallWindowProc 转发。
    """
    global _sub_proc, _def_subclass, _prev_proc
    if not hwnd or _sub_proc is not None:
        return False
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, ctypes.c_uint,
                                 ctypes.c_size_t, LRESULT)
    try:
        comctl = ctypes.windll.comctl32
        _def_subclass = comctl.DefSubclassProc
        _def_subclass.restype = LRESULT
        _def_subclass.argtypes = [wt.HWND, ctypes.c_uint, ctypes.c_size_t, LRESULT]

        def proc(h, msg, wparam, lparam):
            r = _resize_msg(h, msg, wparam, lparam, h)
            if r is not None:
                return r
            return _def_subclass(h, msg, wparam, lparam)

        _sub_proc = WNDPROC(proc)
        comctl.SetWindowSubclass.argtypes = [wt.HWND, WNDPROC, ctypes.c_size_t,
                                             ctypes.c_size_t]
        comctl.SetWindowSubclass.restype = wt.BOOL
        if comctl.SetWindowSubclass(hwnd, _sub_proc, _SUBCLASS_ID, 0):
            return True
    except Exception:
        pass

    # 退化路径：直接换窗口过程
    try:
        user32.SetWindowLongPtrW.restype = ctypes.c_void_p
        user32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_void_p]
        user32.CallWindowProcW.restype = LRESULT
        user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wt.HWND,
                                           ctypes.c_uint, ctypes.c_size_t, LRESULT]

        def hook(h, msg, wparam, lparam):
            r = _resize_msg(h, msg, wparam, lparam, h)
            if r is not None:
                return r
            return user32.CallWindowProcW(_prev_proc, h, msg, wparam, lparam)

        _sub_proc = WNDPROC(hook)
        prev = user32.SetWindowLongPtrW(hwnd, GWLP_WNDPROC,
                                        ctypes.cast(_sub_proc, ctypes.c_void_p))
        if not prev:
            _sub_proc = None
            return False
        _prev_proc = ctypes.c_void_p(prev)
        return True
    except Exception:
        _sub_proc = None
        return False


_HOOKED_CHILD_CLASSES = ("Chrome_RenderWidgetHostHWND", "Chrome_WidgetWin_0",
                         "Chrome_WidgetWin_1")
_child_hooked = set()
_child_keep = []          # 保持回调对象存活，否则回收后会崩在窗口过程里


def _install_child_resize(parent) -> int:
    """把 WebView2 的渲染子窗口也子类化。

    这个子窗口铺满整个客户区，鼠标命中它而不是父窗口，所以只 hook 父窗口时
    WM_NCHITTEST 根本收不到消息（表现为「边缘光标都不变、拖不动」）。
    """
    if not parent:
        return 0
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, ctypes.c_uint,
                                 ctypes.c_size_t, LRESULT)
    ENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    found = []

    def _cb(h, _lp):
        buf = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(h, buf, 128)
        if buf.value in _HOOKED_CHILD_CLASSES:
            found.append(h)
        return True

    try:
        user32.EnumChildWindows(parent, ENUMPROC(_cb), 0)
    except Exception:
        return 0

    user32.GetWindowLongPtrW.restype = ctypes.c_void_p
    user32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
    user32.SetWindowLongPtrW.restype = ctypes.c_void_p
    user32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_void_p]
    user32.CallWindowProcW.restype = LRESULT
    user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wt.HWND,
                                       ctypes.c_uint, ctypes.c_size_t, LRESULT]

    def _make_proc(prev_proc):
        def _proc(ch, msg, wparam, lparam):
            r = _resize_msg(ch, msg, wparam, lparam, parent)
            if r is not None:
                return r
            return user32.CallWindowProcW(prev_proc, ch, msg, wparam, lparam)
        return WNDPROC(_proc)

    done = 0
    for h in found:
        if h in _child_hooked:
            continue
        try:
            prev = user32.GetWindowLongPtrW(h, GWLP_WNDPROC)
            if not prev:
                continue
            proc = _make_proc(ctypes.c_void_p(prev))
            user32.SetWindowLongPtrW(h, GWLP_WNDPROC,
                                     ctypes.cast(proc, ctypes.c_void_p))
            _child_keep.append(proc)
            if user32.GetWindowLongPtrW(h, GWLP_WNDPROC):
                _child_hooked.add(h)
                done += 1
        except Exception:
            continue
    return done


class ShellApi:
    """暴露给前端 window.pywebview.api 的桌面能力。

    注意：公开属性会被 pywebview 的 JS 桥递归扫描（会深入 .NET 窗体树并挂死），
    因此窗口引用必须保留下划线前缀的私有属性。
    """

    def __init__(self):
        self._win = None

    def bind(self, window):
        self._win = window
        return self

    # 前端主题切换时同步标题栏
    def set_titlebar_theme(self, dark):
        _set_titlebar_dark(self._win, bool(dark))
        return True

    def set_title(self, text):
        try:
            self._win.set_title(text)
        except Exception:
            pass
        return True

    def minimize(self):
        self._win.minimize()
        return True

    def start_resize(self, edge, sx=None, sy=None):
        """前端边缘热区按下时调用：接管鼠标并开始自控缩放。

        edge 用位标记传递：1=左 2=右 4=上 8=下（可组合出四角）。
        WebView2 的渲染子窗口铺满客户区且自己返回 HTCLIENT，原生命中测试到不了
        父窗口，所以由前端判断边缘、这里 SetCapture 抢过鼠标再算尺寸。
        """
        bit = int(edge or 0)
        hit = {(1): HT_LEFT, (2): HT_RIGHT, (4): HT_TOP, (8): HT_BOTTOM,
               (1 | 4): HT_TOPLEFT, (1 | 8): HT_BOTTOMLEFT,
               (2 | 4): HT_TOPRIGHT, (2 | 8): HT_BOTTOMRIGHT}.get(bit)
        if not hit:
            return False
        try:
            hwnd = int(self._win.native.Handle.ToInt64())
        except Exception:
            return False
        trace.mark(f"start_resize edge={bit} -> {hit} sx={sx} sy={sy}")
        # SetCapture 只对调用线程的窗口有效：JS 桥跑在非 UI 线程，必须切回窗体线程
        try:
            from System import Action
            self._win.native.BeginInvoke(
                Action(lambda: _drag_begin(hwnd, hit, sx, sy)))
        except Exception:
            _drag_begin(hwnd, hit, sx, sy)
        return True

    def toggle_maximize(self):
        try:
            SW_MAXIMIZE, SW_RESTORE = 3, 9
            hwnd = int(self._win.native.Handle.ToInt64())
            show_cmd = _window_show_cmd(hwnd)
            # 3 = SW_SHOWMAXIMIZED → 还原；否则最大化
            target = SW_RESTORE if show_cmd == 3 else SW_MAXIMIZE
            ctypes.windll.user32.ShowWindow(hwnd, target)
        except Exception:
            pass
        return True

    def exit_app(self):
        self._win.destroy()
        return True

    def compact_ready(self):
        """紧凑小窗：前端把面板画好了，可以把它从屏幕外挪到中央了。

        屏幕外热身 + 这个信号 = 用户第一眼看到的就是完整面板，
        而不是先出现一块"什么都没有"的底色再变成界面。
        """
        cb = getattr(self, "_on_compact_ready", None)
        if not cb:
            return False
        threading.Thread(target=cb, name="cse-compact-place",
                         daemon=True).start()
        return True

    def set_compact(self, on):
        """切紧凑模式（只显示一个面板）/ 回常规主界面。

        前端在右键压缩/解压流程里调用：任务开始或取消时决定接下来给用户看什么。
        """
        on = bool(on)
        COMPACT["on"] = on
        try:
            hwnd = int(self._win.native.Handle.ToInt64())
        except Exception:
            return False
        if on:
            _resize_centered(hwnd, *COMPACT["size"])
        else:
            normal = getattr(self, "_normal_size", None) or DEFAULT_SIZE
            _resize_centered(hwnd, int(normal[0]), int(normal[1]))
        trace.mark(f"切紧凑模式={on}")
        return True


def _save_geometry(window):
    try:
        if COMPACT["on"]:
            return      # 紧凑小窗的尺寸不是用户想要的默认尺寸，别记它
        native = window.native
        hwnd = int(native.Handle.ToInt64())
        show_cmd = _window_show_cmd(hwnd)
        win_cfg = {"maximized": show_cmd == 3, "v": GEOMETRY_VERSION}
        if show_cmd == 3:
            # 最大化时记录"还原后"的正常边界（rcNormalPosition）
            wp = WINDOWPLACEMENT()
            wp.length = ctypes.sizeof(WINDOWPLACEMENT)
            if not ctypes.windll.user32.GetWindowPlacement(hwnd, byref(wp)):
                return
            r = wp.rcNormalPosition
            win_cfg.update({"x": int(r.Left), "y": int(r.Top),
                            "w": int(r.Right - r.Left), "h": int(r.Bottom - r.Top)})
        elif show_cmd == 2:
            return  # 最小化状态不覆盖记忆
        else:
            # 用 GetWindowRect 取**外框**尺寸（WinForms Size 与真实外框有几像素差）
            r = wt.RECT()
            if not ctypes.windll.user32.GetWindowRect(hwnd, byref(r)):
                return
            win_cfg.update({"x": int(r.left), "y": int(r.top),
                            "w": int(r.right - r.left), "h": int(r.bottom - r.top)})
        settings.save({"window": win_cfg})
    except Exception:
        pass


# ---------------- 入口 ----------------
def _compact_intent() -> str:
    """这次启动是不是"只要一个面板"的小窗；是的话返回 'add' / 'extract'。

    资源管理器右键「压缩」/「解压」时，app_entry 在导入 desktop 之前就把请求塞进了
    服务端的待办队列，所以这里能提前看到。双击归档（view）不算，仍按常规开窗。
    """
    try:
        from .server import pending_launch_actions
        acts = pending_launch_actions()
    except Exception:
        return ""
    if "view" in acts:        # 打开归档看内容：要主界面
        return ""
    if "add" in acts:
        return "add"
    if "extract" in acts:
        return "extract"
    return ""


def _crash_log(msg: str):
    try:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        d = os.path.join(base, "CSE Compressor")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "crash.log"), "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except OSError:
        pass


def main(port=8770, debug=False):
    trace.reset()
    trace.mark("desktop.main 进入")
    if os.name != "nt":
        # 非 Windows：退化为浏览器模式
        from .server import main as server_main
        return server_main(open_browser=True, port=port)

    try:
        return _run(port, debug)
    except Exception:
        import traceback
        _crash_log("[desktop] " + traceback.format_exc())
        raise


def _run(port, debug):
    trace.mark("进入 _run")
    if not _single_instance():
        return 0  # 已唤起既有窗口

    _dpi_aware()
    _app_user_model_id()
    os.environ["CSE_SHELL"] = "1"

    # 服务放到后台线程：窗口不必等引擎模块导入完就能显示（先出启动页）
    srv_holder = {}
    ready = threading.Event()

    def boot_server():
        trace.mark("后台线程开始导入服务")
        from csgui.server import GuiServer
        trace.mark("服务模块导入完成")
        srv = GuiServer(port=port)
        srv.start()
        srv_holder["srv"] = srv
        runtime.write_runtime(srv.port)      # 让后来者秒级找到我（别再盲扫端口）
        trace.mark(f"本地服务已监听 {srv.port}")
        ready.set()

    threading.Thread(target=boot_server, name="cse-server", daemon=True).start()

    import webview
    from csengine import __version__

    trace.mark("webview 导入完成")
    # 无边框窗口：标题栏整行（除按钮/菜单外）都可拖动
    webview.settings["DRAG_REGION_DIRECT_TARGET_ONLY"] = False

    cfg = settings.load()
    win_cfg = cfg.get("window", {}) if isinstance(cfg.get("window"), dict) else {}
    if win_cfg.get("v") != GEOMETRY_VERSION:
        win_cfg = {}          # 旧版记忆作废：回落到新的默认尺寸 860×500
    api = ShellApi()

    # 右键「压缩」/「解压」直接起进程时，第一眼就该是对应面板，不先闪一下主界面。
    # 做法：窗口按小窗尺寸建，页面带 ?compact=1，前端只渲染那一个面板。
    compact_kind = _compact_intent()
    want_compact = bool(compact_kind)
    COMPACT["on"] = want_compact
    COMPACT["kind"] = compact_kind
    COMPACT["size"] = (COMPACT_EXTRACT_SIZE if compact_kind == "extract"
                       else COMPACT_SIZE)
    normal_size = (int(win_cfg.get("w") or DEFAULT_SIZE[0]),
                   int(win_cfg.get("h") or DEFAULT_SIZE[1]))
    size = COMPACT["size"] if want_compact else normal_size
    # 紧凑小窗先放在虚拟桌面之外（用户看不见），等面板画好再挪到中央。
    # 直接居中建窗的话，WebView2 出画前的 200~400ms 就是一块纯底色，
    # 看起来像"压缩界面什么都没显示，闪了一下"。
    off_x, off_y = _offscreen_pos(*size) if want_compact else (None, None)

    window = webview.create_window(
        f"CSE Compressor {__version__}",
        html=_splash_html(),     # 先显示启动页，服务就绪后换成正式界面
        js_api=api,
        width=size[0],
        height=size[1],
        x=off_x if want_compact else win_cfg.get("x"),
        y=off_y if want_compact else win_cfg.get("y"),
        min_size=COMPACT_MIN if want_compact else MIN_SIZE,
        resizable=True,
        maximized=False if want_compact else bool(win_cfg.get("maximized")),
        frameless=True,          # 去掉原生外框，界面一体化
        easy_drag=False,         # 仅标题栏拖拽区可拖动
        shadow=True,             # 保留系统阴影/圆角
        hidden=True,             # 关键：先建窗不显示，启动页画出首帧后再亮出来
        background_color=_splash_bg(),   # WebView2 出画前铺的底色 = 启动页底色
    )
    api.bind(window)
    api._normal_size = normal_size
    trace.mark("窗口对象已创建" + (
        {"add": "（紧凑：只显示压缩对话框）",
         "extract": "（紧凑：只显示解压进度）"}.get(compact_kind, "")
        if want_compact else ""))

    app_state = {"hwnd": 0, "revealed": False, "compact": want_compact,
                 "placed": False, "frame_ready": False, "reveal_started": False}

    def _apply_frame():
        """补样式位 / 装边缘拉伸 / 记下 hwnd（可重复调用）。

        正常流程里由 events.shown 触发；紧凑小窗不用 window.show()（那会抢焦点），
        所以由 _reveal 主动调一次。
        """
        hwnd = _enable_frame_behaviour(window)
        app_state["hwnd"] = hwnd
        if hwnd:
            ok = _install_resize(hwnd)
            n_child = _install_child_resize(hwnd)
            trace.mark(f"边缘拉伸安装={'OK' if ok else 'FAIL'} 子窗口={n_child}")
            app_state["frame_ready"] = True
        return hwnd

    def _reveal():
        """把窗口亮出来。

        之前的做法是建窗即显示：WebView2 要 200~400ms 才画出第一帧，
        这段时间窗口里只有 WinForms 铺的纯色底，看起来就是"闪一下全黑"。
        现在窗口以 hidden 状态创建，等启动页真的渲染完（loaded 事件）再显示，
        第一眼看到的就是启动页本身。

        紧凑小窗更严格：它建在屏幕外，这里先"屏幕外显示 + 不抢焦点"——
        抢焦点会让资源管理器里选中的文件先褪色闪一下。等内容画好再由
        _place_compact() 挪进屏幕中央。
        """
        if want_compact:
            hwnd = app_state.get("hwnd") or _hwnd_of(window)
            if hwnd:
                user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
                if not app_state.get("frame_ready"):
                    _apply_frame()
                return True
        try:
            window.show()
            return True
        except Exception:
            return False

    def _reveal_when_painted():
        """守护线程：反复尝试显示，直到窗口真的可见（防 loaded 事件不触发）。"""
        for _ in range(60):
            if app_state.get("revealed"):
                return
            _reveal()
            hwnd = app_state.get("hwnd") or _find_existing_window()
            if hwnd and user32.IsWindowVisible(hwnd):
                app_state["revealed"] = True
                trace.mark("窗口已显示（启动页首帧就绪）")
                if want_compact:
                    trace.mark("紧凑小窗停在屏幕外，等面板画好再挪入")
                return
            time.sleep(0.1)
        trace.mark("窗口显示守护超时")

    def _place_compact(reason="面板就绪"):
        """把紧凑小窗从屏幕外挪到屏幕中央（等它真的画出内容再挪）。"""
        if not want_compact or app_state.get("placed"):
            return
        hwnd = app_state.get("hwnd") or _hwnd_of(window)
        if not hwnd:
            return
        if not user32.IsWindowVisible(hwnd):
            _reveal()
        waited = 0.0
        while waited < 1.2:
            if _painted(hwnd):
                break
            time.sleep(0.05)
            waited += 0.05
        _move_centered(hwnd, *COMPACT["size"], activate=True)
        app_state["placed"] = True
        trace.mark(f"紧凑窗口已就位（{reason}，等待 {int(waited * 1000)}ms）")

    def _place_backstop():
        """兜底：前端没上报就绪（脚本出错等），也不能让用户等到一片空白。"""
        time.sleep(3.0)
        if not app_state.get("placed"):
            _place_compact("兜底")

    api._on_compact_ready = lambda: _place_compact("前端已就绪")

    def on_shown():
        """窗口真正显示后：补样式位、装边缘拉伸、校正尺寸。"""
        trace.mark("窗口已显示")
        hwnd = _apply_frame()
        if hwnd:
            if app_state.get("compact"):
                _set_outer_size(hwnd, *COMPACT["size"])
                trace.mark("紧凑窗口尺寸已校正（仍在屏幕外）")
                if app_state.get("placed"):
                    _move_centered(hwnd, *COMPACT["size"])
            elif not win_cfg.get("maximized"):
                _set_outer_size(hwnd, *normal_size)
                trace.mark("窗口尺寸已校正")

    def on_loaded():
        """WebView2 可能重建渲染子窗口，每次载入后补装一次（重复调用安全）。"""
        app_state["loads"] = app_state.get("loads", 0) + 1
        trace.mark("启动页首帧就绪（loaded）" if app_state["loads"] == 1
                   else f"第 {app_state['loads']} 次载入完成")
        hwnd = app_state.get("hwnd")
        if hwnd:
            trace.mark(f"载入后补装子窗口拉伸={_install_child_resize(hwnd)}")
        # 首帧（启动页）已渲染，可以亮窗口了（只起一条线程，重复 loaded 不再叠加）
        if not app_state.get("reveal_started"):
            app_state["reveal_started"] = True
            threading.Thread(target=_reveal_when_painted, name="cse-reveal",
                             daemon=True).start()


    window.events.shown += on_shown
    window.events.loaded += on_loaded
    window.events.closing += (lambda: _save_geometry(window))

    def after_start():
        """GUI 循环启动后（独立线程）：服务就绪就把启动页换成正式界面。"""
        # 兜底：万一 WebView2 的 loaded 事件没来（渲染进程异常），1.2 秒后强显
        def _backstop():
            time.sleep(1.2)
            _reveal_when_painted()

        threading.Thread(target=_backstop, name="cse-reveal-backstop", daemon=True).start()
        if not ready.wait(20):
            trace.mark("服务启动超时")
            return
        srv = srv_holder.get("srv")
        if not srv:
            return
        try:
            url = srv.url()
            if want_compact:
                url += f"?compact=1&kind={compact_kind}"
            window.load_url(url)
            trace.mark("已载入正式界面" + ("（紧凑模式）" if want_compact else ""))
            hwnd = app_state.get("hwnd")
            if hwnd:
                trace.mark(f"载入正式界面后补装子窗口拉伸={_install_child_resize(hwnd)}")
            if want_compact:
                threading.Thread(target=_place_backstop, name="cse-compact-backstop",
                                 daemon=True).start()
        except Exception:
            trace.mark("载入正式界面失败")

    try:
        webview.start(after_start, icon=_icon_path() or None, debug=debug,
                      gui="edgechromium",   # 明确指定后端，省掉平台探测
                      private_mode=False,
                      storage_path=os.path.join(
                          os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                          "CSE Compressor", "webview"))
        trace.mark("GUI 循环退出")
    finally:
        _save_geometry(window)
        srv = srv_holder.get("srv")
        if srv:
            srv.stop()
        runtime.clear_runtime()
    return 0


def _resolve_dark(theme: str) -> bool:
    if theme == "light":
        return False
    if theme == "system":
        try:  # 读系统偏好
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
                val, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")
            return not bool(val)
        except OSError:
            return True
    return True  # dark


if __name__ == "__main__":
    sys.exit(main())
