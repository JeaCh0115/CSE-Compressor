# -*- coding: utf-8 -*-
"""生成 CSE Compressor 的应用图标与压缩包文件图标族。

设计语言（v2）：
  文件图标 —— 两张白纸叠层（后层右上偏移），右上角按 45° 切线缺一角，
             渐变描边勾轮廓，左下压一块标签牌写格式名；9 种格式各一套配色。
  软件图标 —— 实心文件夹（带左侧标签页）+ 前层压缩面板，10 单位白缝分层，
             竖缝是拉链，4 道齿 + 底部拱形拉头，靛蓝纵向渐变。

尺寸分档：
  文件图标  >=80 全细节（白底标签牌 + 渐变字）；32–64 描边加粗 + 实心色牌无字；
            16–24 降到填充版，描边保底 1.45px。
  软件图标  >=80 四齿 + 拱；48–64 三齿 + 拱；24–47 两齿无拱；<24 只留竖缝。
            小尺寸改用 BOX 重采样（LANCZOS 会发虚）。

产出（assets/icons/）：
  cse-app.ico / cse-app-256.png   应用图标（窗口、任务栏、安装包、快捷方式）
  file-<fmt>.ico                  各格式文件图标（资源管理器用）
  file-cse-256.png                独家格式大图（安装器 / 展示用）
  contact-sheet.png               预览总览（供人工检查）
  favicon.png / app-logo.png      界面 favicon 与关于页 / 安装器 logo
  logo-22.png / logo-44.png       界面标题栏左上角 logo（1x / 2x）

标题栏 logo 用位图而不是内联 SVG：22px 下只有位图能保住「文件夹 + 拉链」
的形态（SVG 的 1px 级线条会被抗锯齿糊成一片灰）。两档固定 teeth=2、fill=0.97，
保证 1x / 2x 切换时外观不跳。
"""

import functools
import math
import os

from PIL import Image, ImageChops, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets", "icons")

ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]

FONT_BOLD = r"C:\Windows\Fonts\segoeuib.ttf"
FONT_UI = r"C:\Windows\Fonts\msyh.ttc"

# 扩展名 -> (标签文字, 起始色, 结束色, 说明)
FORMATS = [
    ("cse", "CSE", "#5b6cf9", "#4338ca", "独家格式"),
    ("zip", "ZIP", "#fbbf24", "#ea580c", "通用压缩"),
    ("7z", "7Z", "#a855f7", "#6b21a8", "高压缩比"),
    ("rar", "RAR", "#f87171", "#b91c1c", "通用压缩"),
    ("tar", "TAR", "#b8c2cc", "#3f4753", "仅归档"),
    ("gz", "GZ", "#a3e635", "#4d7c0f", "Gzip"),
    ("bz2", "BZ2", "#34d399", "#065f46", "Bzip2"),
    ("xz", "XZ", "#38bdf8", "#0369a1", "LZMA2"),
    ("iso", "ISO", "#f472d0", "#a21caf", "光盘镜像"),
]

# 软件图标配色 A（靛蓝，CSE 主色）；纵向为主、带一点横向偏移，避免死板
APP_C0, APP_C1 = "#6676ff", "#3a2fb5"
APP_WX, APP_WY = 0.22, 0.78


def hexc(s):
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


@functools.lru_cache(maxsize=64)
def _grad_src(c0, c1, n=256, wx=0.8, wy=0.2):
    """线性渐变源图（t = wx·x + wy·y，都是归一化坐标）。

    只按 256² 生成一次，再按目标尺寸拉伸；线性渐变做线性插值是精确的，不会失真。
    """
    buf = bytearray(n * n * 3)
    denom = float(n - 1) if n > 1 else 1.0
    i = 0
    for y in range(n):
        ty = wy * y / denom
        for x in range(n):
            t = wx * x / denom + ty
            if t > 1.0:
                t = 1.0
            buf[i] = int(c0[0] + (c1[0] - c0[0]) * t)
            buf[i + 1] = int(c0[1] + (c1[1] - c0[1]) * t)
            buf[i + 2] = int(c0[2] + (c1[2] - c0[2]) * t)
            i += 3
    return Image.frombytes("RGB", (n, n), bytes(buf))


def grad(w, h, c0, c1, wx=0.8, wy=0.2):
    return _grad_src(tuple(hexc(c0)), tuple(hexc(c1)), 256, wx, wy).resize(
        (w, h), Image.BILINEAR)


def _quad(p0, p1, p2, steps=8):
    out = []
    for i in range(steps + 1):
        t = i / steps
        m = 1 - t
        out.append((m * m * p0[0] + 2 * m * t * p1[0] + t * t * p2[0],
                    m * m * p0[1] + 2 * m * t * p1[1] + t * t * p2[1]))
    return out


def stroke_mask(size, pts, width):
    """沿闭合路径打等宽描边：逐段粗线 + 每个顶点补圆，拐点不留缝。

    不能用 ImageDraw.line(joint="curve")——45° 拐角处会露出白色缺口。
    """
    w, h = size
    m = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(m)
    wd = max(1, int(round(width)))
    rr = wd / 2.0
    seq = pts + [pts[0]]
    for i in range(len(seq) - 1):
        d.line([seq[i], seq[i + 1]], fill=255, width=wd)
    for px, py in pts:
        d.ellipse((px - rr, py - rr, px + rr, py + rr), fill=255)
    return m


# ==========================================================================
# 文件图标：两纸叠层 + 缺角切线 + 渐变描边 + 标签牌
# ==========================================================================
G = 1024                      # 设计网格
STROKE = 25                   # 描边宽度（网格单位）
RAD = 54                      # 纸角圆角
CUT = 118                     # 缺角切线长度
FW, FH = 600, 700             # 单张纸尺寸
DX, DY = 62, -75              # 后层偏移（右上）
FRONT = (218, 200, 218 + FW, 200 + FH)
BACK = (FRONT[0] + DX, FRONT[1] + DY, FRONT[2] + DX, FRONT[3] + DY)
TAG = (144, 431, 636, 669)    # 标签牌（左缘探出纸面）
TAG_R = 38
DOC_SCALE = 0.98


def rr_points(box, r, cut=0.0, steps=20, cr=0.0):
    """圆角矩形；cut>0 时右上角切成一条 45° 斜边（缺角），cr 为斜边两端过渡圆角。"""
    x0, y0, x1, y1 = box
    r = max(0.0, min(r, (x1 - x0) / 2 - 0.5, (y1 - y0) / 2 - 0.5))
    pts = []

    def arc(cx, cy, rad, a0, a1):
        for i in range(steps + 1):
            a = a0 + (a1 - a0) * i / steps
            pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))

    arc(x0 + r, y0 + r, r, math.pi, 1.5 * math.pi)
    if cut > 0:
        ax, ay = x1 - cut, y0              # 斜边起点（顶边上）
        bx, by = x1, y0 + cut              # 斜边终点（右边上）
        t = max(0.0, min(cr, cut * 0.5))   # 拐点倒角长度（沿边）
        if t > 0.3:
            # 顶边 → 斜边：不越界的二次曲线倒角
            pts += _quad((ax - t, ay), (ax, ay),
                         (ax + t * 0.7071, ay + t * 0.7071))
            # 斜边 → 右边
            pts += _quad((bx - t * 0.7071, by - t * 0.7071), (bx, by),
                         (bx, by + t))
        else:
            pts += [(ax, ay), (bx, by)]
    else:
        arc(x1 - r, y0 + r, r, 1.5 * math.pi, 2 * math.pi)
    arc(x1 - r, y1 - r, r, 0, 0.5 * math.pi)
    arc(x0 + r, y1 - r, r, 0.5 * math.pi, math.pi)
    return pts


def paint(dst, box, r, cut, stroke, grad_img, fill=(255, 255, 255), cr=None):
    """白填充 + 渐变描边的一层；右上角是缺角切线。"""
    w, h = dst.size
    cr = stroke * 0.5 if cr is None else cr
    pts = rr_points(box, r, cut, cr=cr)

    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).polygon(pts, fill=255)
    dst.paste(fill, mask=m)

    dst.paste(grad_img, mask=stroke_mask((w, h), pts, max(1, int(round(stroke)))))


def text_sprite(text, font, tracking=0.0):
    """按文字实际像素包围盒裁出的蒙版，便于做真正的居中。

    旧写法按字符宽度累加再手减偏移会整体偏几像素；这里以 bbox 中心对齐。
    """
    probe = ImageDraw.Draw(Image.new("L", (4, 4)))
    widths = [probe.textlength(ch, font=font) for ch in text]
    total = sum(widths) + tracking * max(0, len(text) - 1)
    asc, desc = font.getmetrics()
    pad = int(font.size * 0.35) + 6
    W = int(math.ceil(total)) + pad * 2
    H = asc + desc + pad * 2
    m = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(m)
    x = float(pad)
    for ch, cw in zip(text, widths):
        d.text((x, pad), ch, font=font, fill=255, anchor="la")
        x += cw + tracking
    bb = m.getbbox()
    return m.crop(bb) if bb else m


def label_sprite(text, box, k, cap_ratio=0.46, tracking_em=0.03, max_ratio=0.82):
    """按标签牌高度定字高（cap 高占比），横向做安全收缩。"""
    tag_w = (box[2] - box[0]) * k
    tag_h = (box[3] - box[1]) * k
    fs = max(6, int(cap_ratio * tag_h / 0.70))
    sp = None
    for _ in range(6):
        font = ImageFont.truetype(FONT_BOLD, max(6, fs))
        sp = text_sprite(text, font, tracking=fs * tracking_em)
        if sp.width <= tag_w * max_ratio:
            break
        fs = int(fs * tag_w * max_ratio / max(1, sp.width) * 0.98)
    return sp


def render_doc(c0, c1, label, size, ss=3, detail=2, tag_solid=False):
    """大尺寸文件图标。detail: 2 带文字 / 1 无文字；tag_solid: 实心牌 + 白字。"""
    w = size * ss
    k = w / G * DOC_SCALE
    cc = G / 2

    def addr(b):
        return tuple(((v - cc) * DOC_SCALE + cc) * (w / G) for v in b)

    img = Image.new("RGBA", (w, w), (0, 0, 0, 0))
    gr = grad(w, w, c0, c1)

    if detail >= 1:
        paint(img, addr(BACK), RAD * k, CUT * k, STROKE * k, gr)
    paint(img, addr(FRONT), RAD * k, CUT * k, STROKE * k, gr)

    tag = addr(TAG)
    if tag_solid:
        m = Image.new("L", (w, w), 0)
        ImageDraw.Draw(m).polygon(rr_points(tag, TAG_R * k), fill=255)
        s2 = STROKE * k / 2
        sm = Image.new("L", (w, w), 0)
        ImageDraw.Draw(sm).polygon(
            rr_points((tag[0] + s2, tag[1] + s2, tag[2] - s2, tag[3] - s2),
                      max(0.0, TAG_R * k - s2)), fill=255)
        img.paste(gr, mask=sm)
    else:
        paint(img, tag, TAG_R * k, 0, STROKE * k, gr)

    if detail >= 2 and label:
        sp = label_sprite(label, TAG, k)
        x = int(round((tag[0] + tag[2]) / 2 - sp.width / 2))
        y = int(round((tag[1] + tag[3]) / 2 - sp.height / 2))
        if tag_solid:
            tile = Image.new("RGBA", sp.size, (255, 255, 255, 255))
        else:
            tile = gr.convert("RGBA").crop((x, y, x + sp.width, y + sp.height))
        img.paste(tile, (x, y), sp)

    return img.resize((size, size), Image.LANCZOS)


def render_doc_small(size, c0, c1, two=True):
    """中小尺寸：加粗描边的线框版 + 实心标签牌（不写字，靠颜色区分）。"""
    ss = 10
    w = size * ss
    k = w / G

    def addr(b):
        return tuple(v * k for v in b)

    img = Image.new("RGBA", (w, w), (0, 0, 0, 0))
    gr = grad(w, w, c0, c1)

    stroke_px = 1.45 if size <= 24 else 1.65
    stroke = max(stroke_px / size, 0.042) * w

    if two:
        paint(img, addr(BACK), RAD * k, CUT * k, stroke, gr)
    paint(img, addr(FRONT), RAD * k, CUT * k, stroke, gr)

    x0, y0, x1, y1 = addr(FRONT)
    pw, ph = x1 - x0, y1 - y0
    bw, bh = pw * 0.68, ph * 0.28
    bx = x0 - pw * 0.08
    by = y0 + ph * 0.50 - bh / 2
    m = Image.new("L", (w, w), 0)
    ImageDraw.Draw(m).polygon(rr_points((bx, by, bx + bw, by + bh), bh * 0.30),
                             fill=255)
    img.paste(gr, mask=m)
    return img.resize((size, size), Image.LANCZOS)


def render_file(fmt, size):
    """按尺寸自动选档：>=80 全细节，其余走加粗线框版。"""
    _, label, c0, c1, _ = fmt
    if size >= 80:
        return render_doc(c0, c1, label, size, ss=3, detail=2)
    return render_doc_small(size, c0, c1, two=size >= 20)


# ==========================================================================
# 软件图标：实心文件夹 + 白缝分层 + 拉链（竖缝 + 齿 + 拱形拉头）
# ==========================================================================
REF_W, REF_H = 255.0, 213.0    # 参考图内容框（单位 = 参考图像素）
APP_FILL = 0.92                # 内容占画布比例
TAB_BOOST = 1.35               # 小尺寸标签页夸张系数

RAD_BACK = 12.0                # 后层底部圆角
TAB_RIGHT_TOP = 72.0           # 标签页顶边末端（斜边起点）
TAB_H = 38.0                   # 标签页高度（= 机身顶边 y）
BACK_RIGHT = 205.0
FRONT_BOX = (50.0, 85.0, 255.0, 213.0)
GAP = 10.0                     # 前后层白缝
SEAM_X = 157.0                 # 拉链竖缝中心
SEAM_W = 11.0
TEETH_W = 37.0
TEETH_H = 10.0
TEETH_SETS = {4: (106.0, 127.0, 148.0, 178.0),
              3: (104.0, 138.0, 176.0),
              2: (106.0, 176.0),
              1: (128.0,),
              0: ()}
ARCH_TOP = 185.0               # 拱腔内顶
ARCH_OUT = 40.0                # 拱外宽
ARCH_LEG = 10.0                # 拱腿宽

_S = APP_FILL * G / REF_W      # 参考单位 → 网格
_OX = (G - REF_W * _S) / 2.0
_OY = (G - REF_H * _S) / 2.0
_TAB_H = TAB_H


def _setup(fill, tab_h=TAB_H):
    """每次渲染都按目标尺寸重算缩放与偏移；之后所有坐标都要再乘 k = w / G。"""
    global _S, _OX, _OY, _TAB_H
    _TAB_H = tab_h
    _S = fill * G / REF_W
    _OX = (G - REF_W * _S) / 2.0
    _OY = (G - REF_H * _S) / 2.0


def _x(v):
    return _OX + v * _S


def _y(v):
    return _OY + v * _S


def _l(v):
    return v * _S


def _rpoly(box, radii, steps=10):
    """四角半径可不同的圆角矩形（顺时针，从左上角起）。radii=(tl, tr, br, bl)。"""
    x0, y0, x1, y1 = box
    tl, tr, br, bl = radii
    pts = [(x0, y0 + tl)]
    pts += _quad((x0, y0 + tl * 0.45), (x0, y0), (x0 + tl, y0), steps)
    pts += [(x1 - tr, y0)]
    pts += _quad((x1 - tr * 0.45, y0), (x1, y0), (x1, y0 + tr), steps)
    pts += [(x1, y1 - br)]
    pts += _quad((x1, y1 - br * 0.45), (x1, y1), (x1 - br, y1), steps)
    pts += [(x0 + bl, y1)]
    pts += _quad((x0 + bl * 0.45, y1), (x0, y1), (x0, y1 - bl), steps)
    return pts


def front_points():
    fx0, fy0, fx1, fy1 = FRONT_BOX
    return _rpoly((_x(fx0), _y(fy0), _x(fx1), _y(fy1)),
                  (_l(3.0), _l(6.0), _l(16.0), _l(16.0)))


def back_points():
    """后层文件夹：标签页顶边 + 右上斜边 + 机身顶边 + 底部大圆角。"""
    X0, X1 = _x(0.0), _x(BACK_RIGHT)
    YT, YB, YM = _y(0.0), _y(REF_H), _y(_TAB_H)
    r_tl, r_tr, r_br, r_bl = _l(7.0), _l(6.0), _l(6.0), _l(RAD_BACK)
    xt = _x(TAB_RIGHT_TOP)         # 标签页顶边末端（斜边起点）
    xb = _x(90.0)                  # 斜边终点（机身顶边）
    pts = [(X0, YT + r_tl)]
    pts += _quad((X0, YT + r_tl - r_tl * 0.55), (X0, YT), (X0 + r_tl, YT))
    pts += [(xt - r_tr, YT)]
    pts += _quad((xt - r_tr * 0.4, YT), (xt, YT), (xt + r_tr, YT + r_tr))
    pts += [(xb, YM)]
    pts += [(X1 - r_br, YM)]
    pts += _quad((X1 - r_br * 0.45, YM), (X1, YM), (X1, YM + r_br))
    pts += [(X1, YB - r_bl)]
    pts += _quad((X1, YB - r_bl * 0.45), (X1, YB), (X1 - r_bl, YB))
    pts += [(X0 + r_bl, YB)]
    pts += _quad((X0 + r_bl * 0.45, YB), (X0, YB), (X0, YB - r_bl))
    return pts


def render_app(size, c0=APP_C0, c1=APP_C1, teeth=None, ss=None,
               back_shade=1.0, fill=None, wx=APP_WX, wy=APP_WY):
    """渲染软件图标。teeth=None 时按尺寸自动分档。"""
    _setup(fill if fill else (0.97 if size <= 32 else (0.95 if size <= 48
                                                      else APP_FILL)),
           tab_h=TAB_H * TAB_BOOST if size <= 32 else TAB_H)
    if teeth is None:
        teeth = (4 if size >= 80 else 3 if size >= 48 else 2 if size >= 24 else 0)
    ss = ss or (8 if size <= 32 else (6 if size <= 64 else 3))
    w = size * ss
    k = w / G

    def X(v):
        return _x(v) * k

    def Y(v):
        return _y(v) * k

    def L(v):
        return _l(v) * k

    if size <= 32:
        # 小尺寸：白缝 / 拉链按输出像素保底，否则被抗锯齿吃掉
        gap = max(GAP * _S, 1.45 * G / size) * k
        seam = max(SEAM_W * _S, 1.45 * G / size) * k
        t_h = max(TEETH_H * _S, 1.15 * G / size) * k
        t_w = max(TEETH_W * _S, 3.2 * G / size) * k
    else:
        gap, seam = L(GAP), L(SEAM_W)
        t_h, t_w = L(TEETH_H), L(TEETH_W)

    img = Image.new("RGBA", (w, w), (0, 0, 0, 0))
    gr = grad(w, w, c0, c1, wx, wy)
    gr_back = (gr.point(lambda v: int(v * back_shade))
               if back_shade != 1.0 else gr)

    # 后层（白缝已分层，一般不再压暗，压暗反而显脏）
    mback = Image.new("L", (w, w), 0)
    ImageDraw.Draw(mback).polygon(
        [(u * k, v * k) for u, v in back_points()], fill=255)
    # 白缝：沿前层轮廓打 2*gap 描边后从后层挖掉，支持四角不等圆角
    fpts = [(u * k, v * k) for u, v in front_points()]
    mback = ImageChops.subtract(mback, stroke_mask((w, w), fpts, gap * 2))
    img.paste(gr_back, mask=mback)

    m = Image.new("L", (w, w), 0)
    ImageDraw.Draw(m).polygon(fpts, fill=255)
    img.paste(gr, mask=m)

    # 白色拉链
    white = Image.new("L", (w, w), 0)
    wd = ImageDraw.Draw(white)
    sx, top, bot = X(SEAM_X), Y(FRONT_BOX[1]), Y(FRONT_BOX[3])
    leg = L(ARCH_LEG)
    if size <= 32:
        leg = max(leg, 1.15 * G / size * k)
    # 拱头是空腔，竖缝必须停在拱顶，画到底会把腔填成「眼睛」
    has_arch = teeth >= 3 and size >= 48
    seam_bot = min(bot, Y(ARCH_TOP) + leg * 0.55) if has_arch else bot
    wd.rounded_rectangle((sx - seam / 2, top, sx + seam / 2, seam_bot),
                         radius=seam / 2, fill=255)
    for cy in TEETH_SETS[teeth]:
        yy = Y(cy)
        wd.rounded_rectangle((sx - t_w / 2, yy - t_h / 2,
                              sx + t_w / 2, yy + t_h / 2),
                             radius=t_h / 2, fill=255)
    if has_arch:
        cr = max((L(ARCH_OUT) - leg) / 2.0, leg * 0.55)
        cy_arch = min(Y(ARCH_TOP) + cr + leg / 2.0, bot)
        wd.line([(sx - cr, bot + leg), (sx - cr, cy_arch)], fill=255,
                width=int(round(leg)))
        wd.line([(sx + cr, bot + leg), (sx + cr, cy_arch)], fill=255,
                width=int(round(leg)))
        wd.arc((sx - cr - leg / 2.0, cy_arch - cr - leg / 2.0,
                sx + cr + leg / 2.0, cy_arch + cr + leg / 2.0),
               180, 360, fill=255, width=int(round(leg)))
    img.paste((255, 255, 255), mask=ImageChops.darker(white, m))

    return img.resize((size, size),
                      Image.BOX if size <= 32 else Image.LANCZOS)


# ==========================================================================
# 打包产出
# ==========================================================================
def build_logo(size):
    """标题栏 logo：固定两齿档（22px 画不下拱），1x / 2x 两档形态一致。"""
    return render_app(size, teeth=2, fill=0.97)


def build_ico(name, render, extra_png=False):
    """把多尺寸帧写进一个 .ico（256 帧作为主图，其余 append）。"""
    frames = [render(s) for s in ICO_SIZES]
    path = os.path.join(OUT, f"{name}.ico")
    frames[-1].save(path, format="ICO",
                    sizes=[(s, s) for s in ICO_SIZES],
                    append_images=frames[:-1])
    if extra_png:
        frames[-1].save(os.path.join(OUT, f"{name}-256.png"))
    return path


def _font(px, path=FONT_UI):
    try:
        return ImageFont.truetype(path, px)
    except OSError:
        return ImageFont.load_default()


def contact_sheet():
    """人工验收用总览：第一行 10 个 128px 图标，第二行软件图标的尺寸阶梯。"""
    cell, pad, cap = 128, 26, 24
    items = [("APP", render_app(128))]
    for fmt in FORMATS:
        items.append((fmt[0].upper(), render_file(fmt, 128)))

    row1_w = len(items) * (cell + pad) + pad
    ladder = [render_app(s) for s in (16, 24, 32, 48, 64, 96, 128, 256)]
    ladder_w = sum(i.width for i in ladder) + 20 * (len(ladder) + 1)
    W = max(row1_w, ladder_w)
    H = pad + cell + cap + pad + 256 + cap + pad
    sheet = Image.new("RGB", (W, H), (247, 247, 247))

    f = _font(16)
    d = ImageDraw.Draw(sheet)
    for i, (label, img) in enumerate(items):
        x = pad + i * (cell + pad)
        sheet.paste(img, (x, pad), img)
        tw = d.textlength(label, font=f)
        d.text((x + (cell - tw) / 2, pad + cell + 4), label, font=f,
               fill=(90, 92, 100))

    y0 = pad + cell + cap + pad
    x = 20
    for img in ladder:
        sheet.paste(img, (x, y0 + (256 - img.height) // 2), img)
        tw = d.textlength(str(img.width), font=f)
        d.text((x + (img.width - tw) / 2, y0 + 256 + 4), str(img.width),
               font=f, fill=(90, 92, 100))
        x += img.width + 20

    path = os.path.join(OUT, "contact-sheet.png")
    sheet.save(path)
    return path


def main():
    os.makedirs(OUT, exist_ok=True)

    build_ico("cse-app", render_app, extra_png=True)
    for fmt in FORMATS:
        build_ico(f"file-{fmt[0]}", lambda s, f=fmt: render_file(f, s),
                  extra_png=(fmt[0] == "cse"))

    # 网页 favicon 与关于页 / 安装器 logo（同步进前端资源目录）
    web = os.path.join(ROOT, "csgui", "web")
    for target in (OUT, web):
        render_app(64).save(os.path.join(target, "favicon.png"))
        render_app(256).save(os.path.join(target, "app-logo.png"))
        build_logo(22).save(os.path.join(target, "logo-22.png"))
        build_logo(44).save(os.path.join(target, "logo-44.png"))

    sheet = contact_sheet()
    print(f"图标输出目录: {OUT}")
    for name in sorted(os.listdir(OUT)):
        print(f"  {name:<22}{os.path.getsize(os.path.join(OUT, name)) / 1024:6.1f} KB")
    print(f"预览: {sheet}")


if __name__ == "__main__":
    main()
