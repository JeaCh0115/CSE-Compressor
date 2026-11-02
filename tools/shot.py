"""纯 ctypes 截屏（不依赖 PIL/win32 库），用于验证桌面窗口。

用法: python tools/shot.py <输出.png>
"""

import ctypes
import ctypes.wintypes as wt
import sys

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

BI_RGB = 0
DIB_RGB_COLORS = 0
SRCCOPY = 0x00CC0020


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def write_bmp_pixels(path, w, h, buf, stride):
    """把自下而上的 BGRA 像素写成 BMP（浏览器等工具可读）。"""
    import struct
    pixel_data_size = stride * h
    header = b"BM" + struct.pack("<IHHI", 14 + 40 + pixel_data_size, 0, 0, 14 + 40)
    info = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 32, 0, pixel_data_size, 0, 0, 0, 0)
    with open(path, "wb") as f:
        f.write(header + info + buf)


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "shot.bmp"
    hwnd = user32.GetDesktopWindow()
    hdc = user32.GetWindowDC(hwnd)
    w = user32.GetSystemMetrics(0)
    h = user32.GetSystemMetrics(1)
    stride = w * 4
    bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, BI_RGB, stride * h, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(stride * h)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    if not gdi32.BitBlt(mem, 0, 0, w, h, hdc, 0, 0, SRCCOPY):
        print("BitBlt 失败", file=sys.stderr)
        return 1
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bmi), DIB_RGB_COLORS)
    write_bmp_pixels(out, w, h, buf.raw, stride)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    print(f"saved {out} {w}x{h}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
