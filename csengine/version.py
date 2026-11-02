"""版本与构建信息（单一真源）。

版本策略：Beta 演进阶段，采用 `主.次.修订-beta.N` 形式，
Windows 文件版本号取四段整数（0, 1, 0, 3），由 tools/build_win.py 写入资源。
（v0.1.0 起大版本号重置：此前为 2.1.0 系列的内部试验版本。）

打包时 tools/build_win.py 会重写 `csengine/_build.py`，
把真实的打包时间与构建号烧进发行件；开发态则由 _build.py 里的默认值兜底。
"""

__version__ = "0.1.0-beta.14"
CHANNEL = "beta"
CHANNEL_LABEL = "Beta 公测版"

# 用于 Windows 资源（四段整数）与比较
VERSION_TUPLE = (0, 1, 0, 14)

PRODUCT = "CSE Compressor"
COMPANY = "CSE Project"
COPYRIGHT = "© 2026 CSE Project · 基于 MIT 许可发布"
HOMEPAGE = "https://example.com/cse-compressor"

try:  # 构建时生成；开发态走默认值
    from ._build import BUILD_TIME, BUILD_ID, BUILD_CHANNEL  # noqa: F401
except Exception:  # pragma: no cover - 开发态兜底
    BUILD_TIME = "开发版（未打包）"
    BUILD_ID = "dev"
    BUILD_CHANNEL = CHANNEL


def version_string(with_channel: bool = True) -> str:
    return __version__ if with_channel else __version__.split("-")[0]


def is_beta() -> bool:
    return CHANNEL in ("beta", "alpha", "rc") or "beta" in __version__


def about_data() -> dict:
    """关于对话框所需的全部事实信息（前端 / CLI 共用）。"""
    import platform
    import sys

    from . import _opensource

    return {
        "product": PRODUCT,
        "version": __version__,
        "channel": CHANNEL,
        "channel_label": CHANNEL_LABEL,
        "build_time": BUILD_TIME,
        "build_id": BUILD_ID,
        "copyright": COPYRIGHT,
        "company": COMPANY,
        "homepage": HOMEPAGE,
        "python": platform.python_version(),
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "frozen": bool(getattr(sys, "frozen", False)),
        "opensource": _opensource.PROJECTS,
        "credits": _opensource.CREDITS,
        "license": _opensource.LICENSE_NOTE,
    }
