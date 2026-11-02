"""CSC — CSE Compressor 命令行工具（v2：多格式 + 独家 CSE-X）。

用法:
  csc c <输入...> [-o 输出] [-f cse|zip|targz|tar|gz|xz|bz2] [-m auto|...] [-p 密码] [--no-solid]
  csc x 归档 [-o 目录] [-p 密码]
  csc l 归档 [-p 密码]
  csc t 归档 [-p 密码]
  csc info <文件...>
"""

import argparse
import os
import sys

from csengine import __version__, converters
from csengine.compressors import METHODS
from csengine.formats import describe_path, human_size, ratio_percent


def _progress(done, total, name):
    sys.stdout.write(f"\r[{done}/{total}] {str(name)[:58]:<58}")
    sys.stdout.flush()
    if done >= total:
        sys.stdout.write("\n")
        sys.stdout.flush()


def cmd_compress(args):
    inputs = [p for p in args.inputs if os.path.exists(p)]
    if not inputs:
        print("错误: 没有有效的输入文件/目录", file=sys.stderr)
        return 2

    out = args.output
    if not out:
        first = inputs[0].rstrip("\\/")
        base = os.path.basename(first) or "archive"
        ext = {"cse": ".cse", "zip": ".zip", "targz": ".tar.gz", "tarbz2": ".tar.bz2",
               "tarxz": ".tar.xz", "tar": ".tar", "gz": ".gz", "bz2": ".bz2",
               "xz": ".xz", "7z": ".7z"}.get(args.format or "cse", ".cse")
        out = os.path.splitext(base)[0] + ext
    if os.path.isdir(out):
        out = os.path.join(out, "archive" + os.path.splitext(out)[1])

    fmt = args.format or converters.guess_format(out)
    label = converters.FORMAT_LABELS.get(fmt, fmt)
    if os.path.exists(out):
        # 命令行没法弹确认框，但至少要说清楚这次是在"覆盖"而不是"新建"
        print(f"注意: 目标已存在，将覆盖 -> {out}")
    print(f"压缩中 -> {out}")
    print(f"  格式: {label} | 方法: {args.method}"
          + (" | 固实压缩" if args.solid else "")
          + (" | 加密" if args.password else ""))
    info = converters.create(out, inputs, method=args.method, password=args.password,
                             solid=args.solid, comment=args.comment, progress=_progress,
                             fmt=fmt)
    entries = converters.list_items(out, args.password)
    total_orig = sum(e.get("size") or 0 for e in entries if not e.get("is_dir"))
    comp = os.path.getsize(out)
    print(f"完成: {len(entries)} 个条目, {human_size(total_orig)} -> {human_size(comp)} "
          f"({ratio_percent(total_orig, comp)})")
    if info.get("blobs"):
        print(f"  数据块 {info['blobs']} 个（去重后只落一份）")
    return 0


def cmd_extract(args):
    dest = args.output or os.path.curdir
    print(f"解压 {args.archive} -> {dest}")
    files = converters.extract(args.archive, dest, password=args.password, progress=_progress)
    print(f"完成: 解出 {len(files)} 个文件")
    return 0


def cmd_list(args):
    items = converters.list_items(args.archive, args.password)
    summary = converters.summary_of(args.archive, args.password)
    print(f"格式: {summary.get('format')}  条目: {len(items)}  "
          f"归档大小: {human_size(os.path.getsize(args.archive))}"
          + ("  [已加密]" if summary.get("encrypted") else ""))
    if summary.get("comment"):
        print(f"备注: {summary['comment']}")
    print("-" * 80)
    print(f"{'方式':<8} {'原始':>11} {'压缩':>11} {'比率':>7}  名称")
    print("-" * 80)
    for e in items:
        if e.get("is_dir"):
            print(f"{'<目录>':<8} {'':>11} {'':>11} {'':>7}  {e['name']}")
        else:
            size, comp = e.get("size"), e.get("comp_size")
            print(f"{str(e.get('method') or '-'):<8} "
                  f"{human_size(size) if size is not None else '—':>11} "
                  f"{human_size(comp) if comp is not None else '—':>11} "
                  f"{ratio_percent(size, comp) if size and comp else '—':>7}  {e['name']}")
    print("-" * 80)
    total = sum(e.get("size") or 0 for e in items if not e.get("is_dir"))
    print(f"共 {len(items)} 个条目，原始合计 {human_size(total)}")
    return 0


def cmd_test(args):
    converters.test(args.archive, password=args.password)
    print("✓ 完整性测试通过（CRC / 认证校验全部正确）")
    return 0


def cmd_info(args):
    for p in args.path:
        info = describe_path(p)
        print(f"路径  : {info['path']}")
        print(f"大小  : {info['size_human']}")
        print(f"类型  : {'目录' if info['is_dir'] else info['format']}")
        print("-" * 44)
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="csc", description="CSE Compressor 命令行工具")
    p.add_argument("--version", action="version", version=f"csc {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("c", help="压缩文件/目录")
    c.add_argument("inputs", nargs="+", help="输入文件或目录")
    c.add_argument("-o", "--output", help="输出路径（缺省自动命名）")
    c.add_argument("-f", "--format", choices=list(converters.FORMAT_LABELS),
                   help="归档格式（缺省按输出扩展名推断，默认 CSE-X）")
    c.add_argument("-m", "--method", default="auto", choices=["auto"] + list(METHODS),
                   help="压缩方法")
    c.add_argument("-p", "--password", help="加密密码（仅 CSE-X 独家格式支持）")
    c.add_argument("--no-solid", dest="solid", action="store_false",
                   help="关闭固实压缩（每文件独立成块）")
    c.add_argument("--comment", default="", help="写入归档的备注")
    c.set_defaults(func=cmd_compress)

    x = sub.add_parser("x", help="解压归档（自动识别格式）")
    x.add_argument("archive", help="归档路径")
    x.add_argument("-o", "--output", help="解压目录（缺省当前目录）")
    x.add_argument("-p", "--password", help="加密归档的密码")
    x.set_defaults(func=cmd_extract)

    l = sub.add_parser("l", help="列出归档内容")
    l.add_argument("archive")
    l.add_argument("-p", "--password")
    l.set_defaults(func=cmd_list)

    t = sub.add_parser("t", help="完整性测试")
    t.add_argument("archive")
    t.add_argument("-p", "--password")
    t.set_defaults(func=cmd_test)

    i = sub.add_parser("info", help="显示文件信息")
    i.add_argument("path", nargs="+")
    i.set_defaults(func=cmd_info)
    return p


def main(argv=None):
    from .console import setup_console

    setup_console()      # 英文系统控制台是 cp1252，不先处理，第一句中文就把命令带走
    argv = list(sys.argv[1:] if argv is None else argv)

    # Windows 资源管理器集成：双击 .cse 打开界面 / 右键"用 CSE Compressor 解压"
    if "--extract" in argv:
        paths = [a for a in argv if not a.startswith("-")]
        if paths and os.path.isfile(paths[0]):
            arc = paths[0]
            dest = os.path.splitext(arc)[0]
            print(f"解压 {arc} -> {dest}")
            files = converters.extract(arc, dest)
            print(f"完成: 解出 {len(files)} 个文件")
            return 0
        print("错误: --extract 需要一个归档路径", file=sys.stderr)
        return 2
    if argv and not argv[0].startswith("-") and os.path.isfile(argv[0]) \
            and converters.guess_format(argv[0]):
        # 把归档路径交给图形界面打开（查看/解压）
        from csgui.desktop import main as desktop_main
        from csgui.server import set_launch_file
        set_launch_file(os.path.abspath(argv[0]))
        return desktop_main()

    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, FileNotFoundError, EOFError, OSError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
