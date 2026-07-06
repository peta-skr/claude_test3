"""An interactive terminal user interface for pybrowser.

Because the sandbox has no graphical display, the browser is driven from the
terminal: the page is rendered to plain text, links are numbered, and simple
commands navigate.  It behaves like a tiny text-mode browser (think w3m/lynx)
on top of the very same engine that produces the pixel-perfect PNGs.

Commands::

    <url>          navigate to a URL (http/https/file/data/about)
    <number>       follow the link with that index
    b / back       go back            f / fwd      go forward
    r / reload     reload             links        list links only
    scroll / more  show the next screenful
    save [file]    save a PNG screenshot (default screenshot.png)
    tabs           list tabs          tab <n>      switch tab
    newtab <url>   open a new tab      close        close current tab
    help           show this help      q / quit     exit
"""

from __future__ import annotations

import sys
from typing import Optional

from .browser import Browser

_HELP = __doc__


def _print_page(browser: Browser, page_lines: int = 40) -> None:
    tab = browser.tab
    text = tab.render_text()
    lines = text.splitlines()
    start = getattr(tab, "_tui_offset", 0)
    window = lines[start:start + page_lines]
    print("\n".join(window))
    tab._tui_offset = start + page_lines  # type: ignore[attr-defined]
    remaining = len(lines) - (start + page_lines)
    print("-" * 60)
    print(f"[{tab.title}]  {tab.url}")
    if remaining > 0:
        print(f"({remaining} more lines - type 'more' to continue)")
    if tab.links:
        print(f"{len(tab.links)} link(s) - type a number to follow, "
              f"'links' to list.")
    if tab.status:
        print(tab.status)


def _list_links(browser: Browser) -> None:
    tab = browser.tab
    if not tab.links:
        print("(no links on this page)")
        return
    for i, (href, text) in enumerate(tab.links):
        label = (text or href)
        if len(label) > 60:
            label = label[:57] + "..."
        print(f"  [{i:>3}] {label}")


def run_tui(start_url: str = "about:home", width: int = 100) -> int:
    browser = Browser(width=width * 6, height=600)
    browser.new_tab(start_url)
    print("pybrowser - a browser built from scratch. Type 'help' for commands.\n")
    _print_page(browser)

    while True:
        try:
            raw = input("\npybrowser> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not raw:
            continue
        cmd, _, arg = raw.partition(" ")
        cmd_l = cmd.lower()
        arg = arg.strip()
        tab = browser.tab

        if cmd_l in ("q", "quit", "exit"):
            return 0
        elif cmd_l in ("help", "?"):
            print(_HELP)
        elif cmd_l.isdigit():
            if tab.follow_link(int(cmd_l)):
                tab._tui_offset = 0  # type: ignore[attr-defined]
                _print_page(browser)
            else:
                print(f"No link #{cmd_l}.")
        elif cmd_l in ("b", "back"):
            if tab.go_back():
                tab._tui_offset = 0  # type: ignore[attr-defined]
                _print_page(browser)
            else:
                print("Nothing to go back to.")
        elif cmd_l in ("f", "fwd", "forward"):
            if tab.go_forward():
                tab._tui_offset = 0  # type: ignore[attr-defined]
                _print_page(browser)
            else:
                print("Nothing to go forward to.")
        elif cmd_l in ("r", "reload"):
            tab.reload()
            tab._tui_offset = 0  # type: ignore[attr-defined]
            _print_page(browser)
        elif cmd_l == "links":
            _list_links(browser)
        elif cmd_l in ("more", "scroll", "next"):
            _print_page(browser)
        elif cmd_l == "save":
            path = arg or "screenshot.png"
            browser.screenshot().save_png(path)
            print(f"Saved screenshot to {path}")
        elif cmd_l == "tabs":
            for i, t in enumerate(browser.tabs):
                marker = "*" if i == browser.active else " "
                print(f" {marker}[{i}] {t.title}  ({t.url})")
        elif cmd_l == "tab":
            if arg.isdigit():
                browser.switch_tab(int(arg))
                browser.tab._tui_offset = 0  # type: ignore[attr-defined]
                _print_page(browser)
            else:
                print("Usage: tab <n>")
        elif cmd_l == "newtab":
            browser.new_tab(arg or "about:blank")
            browser.tab._tui_offset = 0  # type: ignore[attr-defined]
            _print_page(browser)
        elif cmd_l == "close":
            browser.close_tab()
            if not browser.tabs:
                return 0
            browser.tab._tui_offset = 0  # type: ignore[attr-defined]
            _print_page(browser)
        else:
            # Treat the whole line as a URL to navigate to.
            tab.load(raw)
            tab._tui_offset = 0  # type: ignore[attr-defined]
            _print_page(browser)


if __name__ == "__main__":
    sys.exit(run_tui())
