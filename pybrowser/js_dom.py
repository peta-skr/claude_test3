"""Host objects that bridge the JS interpreter to the DOM.

These wrap :mod:`pybrowser.dom` nodes and expose a small slice of the real
browser API: ``document.getElementById`` / ``querySelector`` /
``createElement``, and element ``textContent`` / ``innerHTML`` /
``getAttribute`` / ``setAttribute`` / ``style`` / ``appendChild``.  Any
mutation flips a dirty flag so the browser knows to re-render.
"""

from __future__ import annotations

import math
from typing import Any, List, Optional

from .css import CSSParser
from .dom import Element, Node, Text
from .html_parser import parse_html


class HostObject:
    """Base class for objects exposing properties/methods to JS."""

    def js_get(self, name: str) -> Any:
        raise KeyError(name)

    def js_set(self, name: str, value: Any) -> None:
        raise KeyError(name)


def host_get(obj: Any, name: str):
    from .js import UNDEFINED, _bi
    if isinstance(obj, HostObject):
        try:
            return obj.js_get(name)
        except KeyError:
            return UNDEFINED
    # Plain Python callables exposed directly.
    if callable(obj) and hasattr(obj, name):
        return _bi(getattr(obj, name))
    return UNDEFINED


def host_set(obj: Any, name: str, value: Any) -> None:
    if isinstance(obj, HostObject):
        obj.js_set(name, value)


# ---------------------------------------------------------------------------
# console / Math / JSON
# ---------------------------------------------------------------------------


class ConsoleObject(HostObject):
    def __init__(self, interp) -> None:
        self.interp = interp

    def js_get(self, name):
        from .js import _bi, to_str
        if name in ("log", "info", "warn", "error", "debug"):
            def _log(*args):
                from .js import UNDEFINED
                self.interp.console_output.append(
                    " ".join(to_str(a) for a in args))
                return UNDEFINED
            return _bi(_log)
        raise KeyError(name)


def make_console(interp):
    return ConsoleObject(interp)


class _Namespace(HostObject):
    def __init__(self, members):
        self._members = members

    def js_get(self, name):
        if name in self._members:
            return self._members[name]
        raise KeyError(name)


def make_math():
    from .js import _bi, to_num
    return _Namespace({
        "PI": math.pi, "E": math.e,
        "floor": _bi(lambda x: float(math.floor(to_num(x)))),
        "ceil": _bi(lambda x: float(math.ceil(to_num(x)))),
        "round": _bi(lambda x: float(math.floor(to_num(x) + 0.5))),
        "abs": _bi(lambda x: float(abs(to_num(x)))),
        "sqrt": _bi(lambda x: math.sqrt(to_num(x)) if to_num(x) >= 0 else float("nan")),
        "pow": _bi(lambda x, y: float(to_num(x) ** to_num(y))),
        "max": _bi(lambda *xs: max((to_num(x) for x in xs), default=float("-inf"))),
        "min": _bi(lambda *xs: min((to_num(x) for x in xs), default=float("inf"))),
        "random": _bi(lambda: 0.42),  # deterministic in this headless engine
        "trunc": _bi(lambda x: float(math.trunc(to_num(x)))),
    })


def make_json():
    from .js import _bi, to_str, UNDEFINED

    def _stringify(v, *_):
        return _json_dump(v)

    def _parse(s, *_):
        return _json_load(to_str(s))

    return _Namespace({"stringify": _bi(_stringify), "parse": _bi(_parse)})


def _json_dump(v) -> str:
    from .js import UNDEFINED
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if v is UNDEFINED:
        return "null"
    if isinstance(v, float):
        return to_str_num(v)
    if isinstance(v, list):
        return "[" + ",".join(_json_dump(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ",".join(f'"{k}":{_json_dump(val)}'
                              for k, val in v.items()) + "}"
    return "null"


def to_str_num(v):
    from .js import to_str
    return to_str(v)


def _json_load(s: str):
    import json
    try:
        return _pyval_to_js(json.loads(s))
    except (ValueError, TypeError):
        return None


def _pyval_to_js(v):
    if isinstance(v, dict):
        return {k: _pyval_to_js(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_pyval_to_js(x) for x in v]
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return float(v)
    return v


# ---------------------------------------------------------------------------
# document + elements
# ---------------------------------------------------------------------------


class Document(HostObject):
    def __init__(self, root: Node, on_mutate) -> None:
        self.root = root
        self.on_mutate = on_mutate

    def _mutated(self):
        if self.on_mutate:
            self.on_mutate()

    def js_get(self, name):
        from .js import _bi, UNDEFINED
        if name == "getElementById":
            return _bi(self._get_by_id)
        if name == "querySelector":
            return _bi(self._query)
        if name == "querySelectorAll":
            return _bi(self._query_all)
        if name == "createElement":
            return _bi(self._create)
        if name == "createTextNode":
            return _bi(lambda t="": ElementWrapper(Text(str(t)), self))
        if name == "title":
            return self._title()
        if name == "body":
            body = self._find_tag("body")
            return ElementWrapper(body, self) if body else UNDEFINED
        if name in ("documentElement",):
            return ElementWrapper(self.root, self)
        raise KeyError(name)

    def js_set(self, name, value):
        from .js import to_str
        if name == "title":
            title = self._find_tag("title")
            if title is not None:
                title.children = [Text(to_str(value), title)]
                self._mutated()

    def _title(self):
        node = self._find_tag("title")
        if node is None:
            return ""
        return "".join(c.text for c in node.children if isinstance(c, Text))

    def _find_tag(self, tag):
        for node in self.root:
            if isinstance(node, Element) and node.tag == tag:
                return node
        return None

    def _get_by_id(self, ident=""):
        from .js import UNDEFINED, to_str
        ident = to_str(ident)
        for node in self.root:
            if isinstance(node, Element) and node.attributes.get("id") == ident:
                return ElementWrapper(node, self)
        return UNDEFINED

    def _query(self, selector=""):
        from .js import UNDEFINED, to_str
        matches = _select(self.root, to_str(selector))
        return ElementWrapper(matches[0], self) if matches else UNDEFINED

    def _query_all(self, selector=""):
        from .js import to_str
        return [ElementWrapper(n, self) for n in _select(self.root, to_str(selector))]

    def _create(self, tag="div"):
        from .js import to_str
        return ElementWrapper(Element(to_str(tag).lower(), {}), self)


class StyleWrapper(HostObject):
    """``element.style`` -- reads/writes inline style declarations."""

    def __init__(self, node: Element, document: "Document") -> None:
        self.node = node
        self.document = document

    def _decls(self):
        decls = {}
        for part in self.node.attributes.get("style", "").split(";"):
            if ":" in part:
                k, v = part.split(":", 1)
                decls[k.strip()] = v.strip()
        return decls

    def _write(self, decls):
        self.node.attributes["style"] = "; ".join(
            f"{k}: {v}" for k, v in decls.items() if v != "")
        self.document._mutated()

    def js_get(self, name):
        from .js import _bi
        if name == "setProperty":
            return _bi(self._set_property)
        prop = _camel_to_kebab(name)
        return self._decls().get(prop, "")

    def js_set(self, name, value):
        from .js import to_str
        decls = self._decls()
        decls[_camel_to_kebab(name)] = to_str(value)
        self._write(decls)

    def _set_property(self, name="", value=""):
        from .js import to_str, UNDEFINED
        decls = self._decls()
        decls[to_str(name)] = to_str(value)
        self._write(decls)
        return UNDEFINED


class ElementWrapper(HostObject):
    def __init__(self, node: Node, document: "Document") -> None:
        self.node = node
        self.document = document

    # -- properties --
    def js_get(self, name):
        from .js import _bi, UNDEFINED
        node = self.node
        if name == "textContent":
            return _text_content(node)
        if name == "innerHTML":
            return _inner_html(node)
        if name == "tagName":
            return node.tag.upper() if isinstance(node, Element) else "#text"
        if name in ("id", "className"):
            attr = "id" if name == "id" else "class"
            return node.attributes.get(attr, "") if isinstance(node, Element) else ""
        if name == "value":
            return node.attributes.get("value", "") if isinstance(node, Element) else ""
        if name == "style":
            return StyleWrapper(node, self.document) if isinstance(node, Element) else UNDEFINED
        if name == "children":
            return [ElementWrapper(c, self.document) for c in node.children
                    if isinstance(c, Element)]
        if name == "parentNode":
            return (ElementWrapper(node.parent, self.document)
                    if node.parent is not None else UNDEFINED)
        if name == "getAttribute":
            return _bi(self._get_attr)
        if name == "setAttribute":
            return _bi(self._set_attr)
        if name == "hasAttribute":
            return _bi(lambda n="": isinstance(node, Element)
                       and str(n) in node.attributes)
        if name == "removeAttribute":
            return _bi(self._remove_attr)
        if name == "appendChild":
            return _bi(self._append_child)
        if name == "removeChild":
            return _bi(self._remove_child)
        if name == "remove":
            return _bi(self._remove)
        if name == "querySelector":
            return _bi(lambda sel="": self._query(sel, False))
        if name == "querySelectorAll":
            return _bi(lambda sel="": self._query(sel, True))
        if name == "addEventListener":
            return _bi(lambda *a: UNDEFINED)  # no event loop in this engine
        raise KeyError(name)

    def js_set(self, name, value):
        from .js import to_str
        node = self.node
        if not isinstance(node, Element) and name not in ("textContent",):
            return
        if name == "textContent":
            node.children = [Text(to_str(value), node)]
        elif name == "innerHTML":
            _set_inner_html(node, to_str(value))
        elif name == "id":
            node.attributes["id"] = to_str(value)
        elif name == "className":
            node.attributes["class"] = to_str(value)
        elif name == "value":
            node.attributes["value"] = to_str(value)
        else:
            return
        self.document._mutated()

    # -- methods --
    def _get_attr(self, name=""):
        from .js import to_str, UNDEFINED
        if isinstance(self.node, Element):
            return self.node.attributes.get(to_str(name), None) or (
                None if to_str(name) not in self.node.attributes else "")
        return UNDEFINED

    def _set_attr(self, name="", value=""):
        from .js import to_str, UNDEFINED
        if isinstance(self.node, Element):
            self.node.attributes[to_str(name)] = to_str(value)
            self.document._mutated()
        return UNDEFINED

    def _remove_attr(self, name=""):
        from .js import to_str, UNDEFINED
        if isinstance(self.node, Element):
            self.node.attributes.pop(to_str(name), None)
            self.document._mutated()
        return UNDEFINED

    def _append_child(self, child):
        from .js import UNDEFINED
        if isinstance(child, ElementWrapper):
            child.node.parent = self.node
            self.node.children.append(child.node)
            self.document._mutated()
            return child
        return UNDEFINED

    def _remove_child(self, child):
        from .js import UNDEFINED
        if isinstance(child, ElementWrapper) and child.node in self.node.children:
            self.node.children.remove(child.node)
            self.document._mutated()
            return child
        return UNDEFINED

    def _remove(self):
        from .js import UNDEFINED
        parent = self.node.parent
        if parent is not None and self.node in parent.children:
            parent.children.remove(self.node)
            self.document._mutated()
        return UNDEFINED

    def _query(self, selector, all_):
        from .js import to_str, UNDEFINED
        matches = _select(self.node, to_str(selector))
        if all_:
            return [ElementWrapper(n, self.document) for n in matches]
        return ElementWrapper(matches[0], self.document) if matches else UNDEFINED


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _select(root: Node, selector: str) -> List[Node]:
    sel = CSSParser._compile_selector(selector.strip())
    if sel is None:
        return []
    return [n for n in root if isinstance(n, Element) and sel.matches(n)]


def _text_content(node: Node) -> str:
    if isinstance(node, Text):
        return node.text
    return "".join(c.text for c in node if isinstance(c, Text))


def _inner_html(node: Node) -> str:
    parts: List[str] = []
    if isinstance(node, Element):
        for child in node.children:
            parts.append(_serialize(child))
    return "".join(parts)


def _serialize(node: Node) -> str:
    if isinstance(node, Text):
        return node.text
    if isinstance(node, Element):
        attrs = "".join(f' {k}="{v}"' for k, v in node.attributes.items())
        inner = "".join(_serialize(c) for c in node.children)
        return f"<{node.tag}{attrs}>{inner}</{node.tag}>"
    return ""


def _set_inner_html(node: Element, html: str) -> None:
    fragment = parse_html(html)
    # parse_html returns an <html> root; graft its body's children (or all
    # meaningful children) under this node.
    body = None
    for n in fragment:
        if isinstance(n, Element) and n.tag == "body":
            body = n
            break
    source = body if body is not None else fragment
    node.children = []
    for child in source.children:
        child.parent = node
        node.children.append(child)


def _camel_to_kebab(name: str) -> str:
    out = []
    for ch in name:
        if ch.isupper():
            out.append("-")
            out.append(ch.lower())
        else:
            out.append(ch)
    return "".join(out)
