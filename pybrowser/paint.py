"""The display list: a flat list of drawing commands the layout produces.

Keeping paint commands separate from layout means the same page can be
painted at different scroll offsets (by translating each command's ``top``)
without re-running layout, exactly like a real browser's compositor.
"""

from __future__ import annotations

from typing import Tuple

from .fonts import Font
from .raster import RGB, Canvas


class PaintCommand:
    def __init__(self, left: int, top: int, right: int, bottom: int) -> None:
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom

    def execute(self, scroll: int, canvas: Canvas) -> None:  # pragma: no cover
        raise NotImplementedError


class DrawRect(PaintCommand):
    def __init__(self, left, top, right, bottom, color: RGB) -> None:
        super().__init__(left, top, right, bottom)
        self.color = color

    def execute(self, scroll: int, canvas: Canvas) -> None:
        canvas.fill_rect(self.left, self.top - scroll,
                         self.right - self.left, self.bottom - self.top,
                         self.color)


class DrawRectOutline(PaintCommand):
    def __init__(self, left, top, right, bottom, color: RGB, thickness: int = 1):
        super().__init__(left, top, right, bottom)
        self.color = color
        self.thickness = thickness

    def execute(self, scroll: int, canvas: Canvas) -> None:
        top = self.top - scroll
        for t in range(self.thickness):
            canvas.draw_rect_outline(self.left + t, top + t,
                                     (self.right - self.left) - 2 * t,
                                     (self.bottom - self.top) - 2 * t,
                                     self.color)


class DrawText(PaintCommand):
    def __init__(self, left, top, text: str, font: Font, color: RGB) -> None:
        super().__init__(left, top, left + font.measure(text),
                         top + font.line_height)
        self.text = text
        self.font = font
        self.color = color

    def execute(self, scroll: int, canvas: Canvas) -> None:
        canvas.draw_text(self.left, self.top - scroll, self.text,
                         self.font, self.color)


class DrawLine(PaintCommand):
    def __init__(self, x0, y0, x1, y1, color: RGB, thickness: int = 1) -> None:
        super().__init__(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1
        self.color = color
        self.thickness = thickness

    def execute(self, scroll: int, canvas: Canvas) -> None:
        if self.y0 == self.y1:  # horizontal
            for t in range(self.thickness):
                canvas.draw_hline(self.x0, self.x1, self.y0 - scroll + t, self.color)
        elif self.x0 == self.x1:  # vertical
            for t in range(self.thickness):
                canvas.draw_vline(self.x0 + t, self.y0 - scroll,
                                  self.y1 - scroll, self.color)


def paint_visible(display_list, scroll: int, canvas: Canvas) -> None:
    """Execute only the commands that fall within the visible viewport."""
    view_top = scroll
    view_bottom = scroll + canvas.height
    for cmd in display_list:
        if cmd.bottom < view_top or cmd.top > view_bottom:
            continue
        cmd.execute(scroll, canvas)
