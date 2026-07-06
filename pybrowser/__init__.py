"""pybrowser — a small web browser engine written from scratch.

The package implements, using only the Python standard library, the core
pieces every browser needs:

* :mod:`pybrowser.url`         -- a network stack (http/https/file/data/about)
* :mod:`pybrowser.html_parser` -- an HTML tokenizer + DOM tree builder
* :mod:`pybrowser.css`         -- a CSS parser, cascade, and inheritance
* :mod:`pybrowser.layout`      -- a block/inline layout engine (box model)
* :mod:`pybrowser.paint`       -- a display list of drawing commands
* :mod:`pybrowser.raster`      -- a software rasterizer + PNG encoder + font
* :mod:`pybrowser.browser`     -- tabs, history, and Chrome-like window chrome
"""

__all__ = [
    "url",
    "dom",
    "html_parser",
    "css",
    "layout",
    "paint",
    "raster",
    "fonts",
    "browser",
]

__version__ = "0.1.0"
