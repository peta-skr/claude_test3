"""Command-line entry points for pybrowser.

Subcommands::

    python -m pybrowser render <url> [-o out.png] [--width W] [--height H] [--full] [--no-chrome]
    python -m pybrowser text   <url> [--width N]
    python -m pybrowser ascii  <url> [--cols N]
    python -m pybrowser repl                       # interactive terminal browser

With no arguments the interactive terminal browser starts on about:home.
"""

from __future__ import annotations

import argparse
import sys

from .browser import CHROME_HEIGHT, Browser, Tab
from .raster import Canvas


def _render(args: argparse.Namespace) -> int:
    if args.no_chrome:
        tab = Tab(width=args.width)
        tab.load(args.url)
        height = int(tab.content_height) if args.full else args.height
        canvas = Canvas(args.width, max(1, height), (255, 255, 255))
        tab.paint(canvas)
        title = tab.title
    else:
        height = args.height
        browser = Browser(args.width, args.height)
        browser.new_tab(args.url)
        if args.full:
            height = CHROME_HEIGHT + int(browser.tab.content_height)
            browser = Browser(args.width, height)
            browser.new_tab(args.url)
        canvas = browser.screenshot()
        title = browser.tab.title
    canvas.save_png(args.output)
    print(f"Rendered {args.url!r} ({title!r}) -> {args.output} "
          f"[{canvas.width}x{canvas.height}]")
    return 0


def _text(args: argparse.Namespace) -> int:
    tab = Tab(width=args.width * 6)
    tab.load(args.url)
    print(f"# {tab.title}\n")
    print(tab.render_text())
    if tab.links:
        print("\nLinks:")
        for i, (href, text) in enumerate(tab.links):
            label = text or href
            print(f"  [{i}] {label}  ->  {href}")
    return 0


def _ascii(args: argparse.Namespace) -> int:
    tab = Tab(width=args.cols * 6)
    tab.load(args.url)
    height = min(int(tab.content_height), 2000)
    canvas = Canvas(tab.width, max(1, height), (255, 255, 255))
    tab.paint(canvas)
    print(canvas.to_ascii(args.cols))
    return 0


def _repl(args: argparse.Namespace) -> int:
    from .tui import run_tui

    return run_tui(args.url, width=args.width)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pybrowser",
        description="A web browser built from scratch on the Python stdlib.",
    )
    sub = parser.add_subparsers(dest="command")

    p_render = sub.add_parser("render", help="render a page to a PNG file")
    p_render.add_argument("url")
    p_render.add_argument("-o", "--output", default="page.png")
    p_render.add_argument("--width", type=int, default=800)
    p_render.add_argument("--height", type=int, default=600)
    p_render.add_argument("--full", action="store_true",
                          help="capture the full page height, not just the viewport")
    p_render.add_argument("--no-chrome", action="store_true",
                          help="render only page content, without the browser UI")
    p_render.set_defaults(func=_render)

    p_text = sub.add_parser("text", help="print a page as plain text")
    p_text.add_argument("url")
    p_text.add_argument("--width", type=int, default=100,
                        help="line width in characters")
    p_text.set_defaults(func=_text)

    p_ascii = sub.add_parser("ascii", help="print an ASCII-art preview of a page")
    p_ascii.add_argument("url")
    p_ascii.add_argument("--cols", type=int, default=100)
    p_ascii.set_defaults(func=_ascii)

    p_repl = sub.add_parser("repl", help="interactive terminal browser")
    p_repl.add_argument("url", nargs="?", default="about:home")
    p_repl.add_argument("--width", type=int, default=100)
    p_repl.set_defaults(func=_repl)

    return parser


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if not argv:
        from .tui import run_tui

        return run_tui("about:home")
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
