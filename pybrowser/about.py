"""Built-in ``about:`` pages served without any network access."""

from __future__ import annotations

_BLANK = "<!doctype html><html><head><title>New Tab</title></head><body></body></html>"

_HOME = """<!doctype html>
<html>
<head><title>pybrowser</title></head>
<body>
  <h1>pybrowser</h1>
  <p>A web browser built entirely <b>from scratch</b> on the Python
     standard library &mdash; its own network stack, HTML parser, CSS
     engine, layout engine and PNG rasterizer.</p>
  <h2>Try it</h2>
  <ul>
    <li><a href="about:version">about:version</a></li>
    <li><a href="https://example.com/">https://example.com/</a></li>
  </ul>
  <p>Type a URL in the omnibox to browse the web.</p>
</body>
</html>"""

_VERSION = """<!doctype html>
<html>
<head><title>About pybrowser</title></head>
<body>
  <h1>pybrowser 0.1.0</h1>
  <p>Engine: pybrowser (from scratch)</p>
  <p>Renderer: software rasterizer + built-in bitmap font</p>
  <p>Network: raw sockets + TLS (http, https, file, data, about)</p>
  <p>Dependencies: none (Python standard library only)</p>
</body>
</html>"""


def about_page(name: str) -> str:
    name = (name or "blank").strip().lower()
    if name in ("", "blank", "newtab"):
        return _BLANK
    if name in ("home", "start"):
        return _HOME
    if name == "version":
        return _VERSION
    return (
        "<!doctype html><html><head><title>about:%s</title></head>"
        "<body><h1>about:%s</h1><p>No such built-in page.</p></body></html>"
        % (name, name)
    )
