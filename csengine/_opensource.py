"""开源项目清单与致谢（关于对话框内容来源）。

只列真实使用到的组件，避免"感谢一堆没用过的东西"。
"""

PROJECTS = [
    {
        "name": "Python",
        "version": "3.13",
        "license": "PSF License 2.0",
        "url": "https://www.python.org/",
        "use": "引擎、命令行与界面服务的运行时",
    },
    {
        "name": "pywebview",
        "version": "6.x",
        "license": "BSD-3-Clause",
        "url": "https://pywebview.flowrl.com/",
        "use": "桌面窗口外壳（Windows 上承载 Edge WebView2）",
    },
    {
        "name": "pythonnet / .NET",
        "version": "3.x / .NET 8",
        "license": "MIT / MIT",
        "url": "https://pythonnet.github.io/",
        "use": "pywebview 的 WinForms 后端桥接",
    },
    {
        "name": "Microsoft Edge WebView2",
        "version": "运行时组件",
        "license": "Microsoft 专有（随系统分发）",
        "url": "https://developer.microsoft.com/microsoft-edge/webview2/",
        "use": "界面渲染内核",
    },
    {
        "name": "zlib / DEFLATE",
        "version": "标准库",
        "license": "zlib License",
        "url": "https://zlib.net/",
        "use": "ZIP / GZIP 压缩后端",
    },
    {
        "name": "bzip2 / liblzma (XZ)",
        "version": "标准库",
        "license": "BSD-like / 0BSD",
        "url": "https://tukaani.org/xz/",
        "use": "BZ2 / XZ 压缩后端",
    },
    {
        "name": "PyInstaller",
        "version": "6.x",
        "license": "GPL-2.0 with exception",
        "url": "https://pyinstaller.org/",
        "use": "Windows 可执行文件与安装程序打包",
    },
    {
        "name": "Pillow",
        "version": "13.x",
        "license": "MIT-CMU",
        "url": "https://python-pillow.org/",
        "use": "应用与文件类型图标渲染",
    },
]

CREDITS = [
    ("7-Zip", "本项目的模块分层（引擎 / 命令行 / 图形界面 / 格式层）直接受益于 7-Zip 的工程结构启发；"
               "其 LZMA 实现也长期是压缩率标杆。"),
    ("RFC 8439", "ChaCha20-Poly1305 规范，CSE-X 的加密流程以其为蓝本，并使用官方测试向量做验证。"),
    ("David A. Huffman", "1952 年的哈夫曼编码论文，本项目自研 lzhuf 后端的理论基础。"),
    ("zlib / Info-ZIP 社区", "DEFLATE、ZIP 与 GZIP 的格式规范与长期维护。"),
    ("xterm.js 风格的前端实践", "本地 Web 界面的交互与可访问性参考（拖拽、键盘导航、动效克制）。"),
]

LICENSE_NOTE = (
    "CSE Compressor 本体以 MIT 许可发布；所依赖的第三方组件遵循各自许可，"
    "详见「开源许可」列表。CSE-X 格式规范随软件一同公开，欢迎实现兼容的读写器。"
)
