"""A small tree-walking JavaScript interpreter.

This is not a spec-compliant engine, but it runs the kind of DOM-scripting
code you find on simple pages: variables, arithmetic and string operators,
``if``/``for``/``while``, functions, arrays and objects, and a handful of
built-ins (``console.log``, ``document.*``, element wrappers).  Scripts run
after the document is parsed; any DOM mutations they make are reflected when
the page is re-styled and re-laid-out.

Pipeline: :class:`Lexer` -> :class:`Parser` (recursive descent, precedence
climbing) -> :class:`Interpreter` (tree walking).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------


class Undefined:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self):
        return "undefined"


UNDEFINED = Undefined()


class JSError(Exception):
    """A runtime or parse error in the interpreted script."""


class _Return(Exception):
    def __init__(self, value):
        self.value = value


class _Break(Exception):
    pass


class _Continue(Exception):
    pass


# ---------------------------------------------------------------------------
# Lexer
# ---------------------------------------------------------------------------

KEYWORDS = {
    "var", "let", "const", "function", "return", "if", "else", "for", "while",
    "true", "false", "null", "undefined", "new", "typeof", "break", "continue",
    "this",
}

# Multi-character punctuators, longest first so the lexer is greedy.
PUNCTUATORS = [
    "===", "!==", "&&", "||", "==", "!=", "<=", ">=", "+=", "-=", "*=", "/=",
    "++", "--", "=>", "(", ")", "{", "}", "[", "]", ";", ",", ".", "+", "-",
    "*", "/", "%", "<", ">", "=", "!", "?", ":",
]


class Token:
    __slots__ = ("kind", "value")

    def __init__(self, kind: str, value: Any) -> None:
        self.kind = kind
        self.value = value

    def __repr__(self):
        return f"Token({self.kind}, {self.value!r})"


class Lexer:
    def __init__(self, source: str) -> None:
        self.s = source
        self.i = 0
        self.n = len(source)

    def tokens(self) -> List[Token]:
        out: List[Token] = []
        while self.i < self.n:
            c = self.s[self.i]
            if c in " \t\r\n":
                self.i += 1
            elif c == "/" and self._peek(1) == "/":
                while self.i < self.n and self.s[self.i] != "\n":
                    self.i += 1
            elif c == "/" and self._peek(1) == "*":
                end = self.s.find("*/", self.i + 2)
                self.i = self.n if end == -1 else end + 2
            elif c in "\"'":
                out.append(self._string(c))
            elif c.isdigit() or (c == "." and self._peek(1).isdigit()):
                out.append(self._number())
            elif c.isalpha() or c == "_" or c == "$":
                out.append(self._identifier())
            else:
                out.append(self._punctuator())
        out.append(Token("EOF", None))
        return out

    def _peek(self, offset: int) -> str:
        j = self.i + offset
        return self.s[j] if j < self.n else ""

    def _string(self, quote: str) -> Token:
        self.i += 1
        chars: List[str] = []
        while self.i < self.n and self.s[self.i] != quote:
            c = self.s[self.i]
            if c == "\\" and self.i + 1 < self.n:
                nxt = self.s[self.i + 1]
                chars.append({"n": "\n", "t": "\t", "r": "\r", "\\": "\\",
                              '"': '"', "'": "'", "`": "`"}.get(nxt, nxt))
                self.i += 2
                continue
            chars.append(c)
            self.i += 1
        self.i += 1  # closing quote
        return Token("STRING", "".join(chars))

    def _number(self) -> Token:
        start = self.i
        if self.s[self.i] == "0" and self._peek(1) in ("x", "X"):
            self.i += 2
            while self.i < self.n and self.s[self.i] in "0123456789abcdefABCDEF":
                self.i += 1
            return Token("NUMBER", float(int(self.s[start:self.i], 16)))
        while self.i < self.n and (self.s[self.i].isdigit() or self.s[self.i] == "."):
            self.i += 1
        if self.i < self.n and self.s[self.i] in "eE":
            self.i += 1
            if self.i < self.n and self.s[self.i] in "+-":
                self.i += 1
            while self.i < self.n and self.s[self.i].isdigit():
                self.i += 1
        return Token("NUMBER", float(self.s[start:self.i]))

    def _identifier(self) -> Token:
        start = self.i
        while self.i < self.n and (self.s[self.i].isalnum()
                                   or self.s[self.i] in "_$"):
            self.i += 1
        word = self.s[start:self.i]
        return Token("KEYWORD" if word in KEYWORDS else "IDENT", word)

    def _punctuator(self) -> Token:
        for p in PUNCTUATORS:
            if self.s.startswith(p, self.i):
                self.i += len(p)
                return Token("PUNCT", p)
        # Unknown character: skip it.
        ch = self.s[self.i]
        self.i += 1
        return Token("PUNCT", ch)


# ---------------------------------------------------------------------------
# Parser  (AST nodes are plain tuples: (kind, ...))
# ---------------------------------------------------------------------------


class Parser:
    def __init__(self, tokens: List[Token]) -> None:
        self.toks = tokens
        self.i = 0

    def parse(self) -> list:
        stmts = []
        while not self._at_end():
            stmts.append(self._statement())
        return stmts

    # -- helpers --
    def _peek(self) -> Token:
        return self.toks[self.i]

    def _at_end(self) -> bool:
        return self._peek().kind == "EOF"

    def _advance(self) -> Token:
        tok = self.toks[self.i]
        if not self._at_end():
            self.i += 1
        return tok

    def _check(self, kind: str, value=None) -> bool:
        t = self._peek()
        return t.kind == kind and (value is None or t.value == value)

    def _match(self, kind: str, value=None) -> bool:
        if self._check(kind, value):
            self._advance()
            return True
        return False

    def _expect(self, kind: str, value=None) -> Token:
        if self._check(kind, value):
            return self._advance()
        t = self._peek()
        raise JSError(f"expected {value or kind}, got {t.value!r}")

    def _consume_semi(self) -> None:
        self._match("PUNCT", ";")

    # -- statements --
    def _statement(self):
        t = self._peek()
        if t.kind == "KEYWORD":
            if t.value in ("var", "let", "const"):
                return self._var_decl()
            if t.value == "function":
                return self._function_decl()
            if t.value == "if":
                return self._if()
            if t.value == "for":
                return self._for()
            if t.value == "while":
                return self._while()
            if t.value == "return":
                self._advance()
                value = None
                if not self._check("PUNCT", ";") and not self._check("PUNCT", "}"):
                    value = self._expression()
                self._consume_semi()
                return ("return", value)
            if t.value == "break":
                self._advance()
                self._consume_semi()
                return ("break",)
            if t.value == "continue":
                self._advance()
                self._consume_semi()
                return ("continue",)
        if self._check("PUNCT", "{"):
            return self._block()
        expr = self._expression()
        self._consume_semi()
        return ("expr", expr)

    def _block(self):
        self._expect("PUNCT", "{")
        stmts = []
        while not self._check("PUNCT", "}") and not self._at_end():
            stmts.append(self._statement())
        self._expect("PUNCT", "}")
        return ("block", stmts)

    def _var_decl(self):
        self._advance()  # var/let/const
        decls = []
        while True:
            name = self._expect("IDENT").value
            init = None
            if self._match("PUNCT", "="):
                init = self._assignment()
            decls.append((name, init))
            if not self._match("PUNCT", ","):
                break
        self._consume_semi()
        return ("var", decls)

    def _function_decl(self):
        self._advance()  # function
        name = self._expect("IDENT").value
        params = self._params()
        body = self._block()
        return ("funcdecl", name, params, body)

    def _params(self):
        self._expect("PUNCT", "(")
        params = []
        if not self._check("PUNCT", ")"):
            while True:
                params.append(self._expect("IDENT").value)
                if not self._match("PUNCT", ","):
                    break
        self._expect("PUNCT", ")")
        return params

    def _if(self):
        self._advance()
        self._expect("PUNCT", "(")
        cond = self._expression()
        self._expect("PUNCT", ")")
        then = self._statement()
        alt = None
        if self._match("KEYWORD", "else"):
            alt = self._statement()
        return ("if", cond, then, alt)

    def _while(self):
        self._advance()
        self._expect("PUNCT", "(")
        cond = self._expression()
        self._expect("PUNCT", ")")
        body = self._statement()
        return ("while", cond, body)

    def _for(self):
        self._advance()
        self._expect("PUNCT", "(")
        if self._check("PUNCT", ";"):
            init = None
            self._advance()
        elif self._check("KEYWORD", "var") or self._check("KEYWORD", "let") \
                or self._check("KEYWORD", "const"):
            init = self._var_decl()
        else:
            init = ("expr", self._expression())
            self._consume_semi()
        cond = None if self._check("PUNCT", ";") else self._expression()
        self._expect("PUNCT", ";")
        update = None if self._check("PUNCT", ")") else self._expression()
        self._expect("PUNCT", ")")
        body = self._statement()
        return ("for", init, cond, update, body)

    # -- expressions (precedence climbing) --
    def _expression(self):
        return self._assignment()

    def _assignment(self):
        left = self._ternary()
        if self._check("PUNCT") and self._peek().value in ("=", "+=", "-=",
                                                           "*=", "/="):
            op = self._advance().value
            right = self._assignment()
            return ("assign", op, left, right)
        return left

    def _ternary(self):
        cond = self._binary(0)
        if self._match("PUNCT", "?"):
            then = self._assignment()
            self._expect("PUNCT", ":")
            alt = self._assignment()
            return ("ternary", cond, then, alt)
        return cond

    _BIN_LEVELS = [
        ("||",), ("&&",), ("==", "!=", "===", "!=="),
        ("<", ">", "<=", ">="), ("+", "-"), ("*", "/", "%"),
    ]

    def _binary(self, level: int):
        if level >= len(self._BIN_LEVELS):
            return self._unary()
        left = self._binary(level + 1)
        while self._check("PUNCT") and self._peek().value in self._BIN_LEVELS[level]:
            op = self._advance().value
            right = self._binary(level + 1)
            left = ("binary", op, left, right)
        return left

    def _unary(self):
        if self._check("PUNCT") and self._peek().value in ("-", "!", "+"):
            op = self._advance().value
            return ("unary", op, self._unary())
        if self._check("PUNCT") and self._peek().value in ("++", "--"):
            op = self._advance().value
            return ("preupdate", op, self._unary())
        if self._match("KEYWORD", "typeof"):
            return ("typeof", self._unary())
        return self._postfix()

    def _postfix(self):
        expr = self._primary()
        while True:
            if self._match("PUNCT", "."):
                name = self._advance().value
                expr = ("member", expr, name)
            elif self._match("PUNCT", "["):
                index = self._expression()
                self._expect("PUNCT", "]")
                expr = ("index", expr, index)
            elif self._check("PUNCT", "("):
                args = self._arguments()
                expr = ("call", expr, args)
            elif self._check("PUNCT") and self._peek().value in ("++", "--"):
                op = self._advance().value
                expr = ("postupdate", op, expr)
            else:
                break
        return expr

    def _arguments(self):
        self._expect("PUNCT", "(")
        args = []
        if not self._check("PUNCT", ")"):
            while True:
                args.append(self._assignment())
                if not self._match("PUNCT", ","):
                    break
        self._expect("PUNCT", ")")
        return args

    def _primary(self):
        t = self._peek()
        if t.kind == "NUMBER":
            self._advance()
            return ("num", t.value)
        if t.kind == "STRING":
            self._advance()
            return ("str", t.value)
        if t.kind == "IDENT":
            self._advance()
            return ("ident", t.value)
        if t.kind == "KEYWORD":
            if t.value == "true":
                self._advance(); return ("bool", True)
            if t.value == "false":
                self._advance(); return ("bool", False)
            if t.value == "null":
                self._advance(); return ("null",)
            if t.value == "undefined":
                self._advance(); return ("undef",)
            if t.value == "this":
                self._advance(); return ("ident", "this")
            if t.value == "function":
                self._advance()
                params = self._params()
                body = self._block()
                return ("funcexpr", params, body)
            if t.value == "new":
                self._advance()
                callee = self._postfix()
                return ("new", callee)
        if self._match("PUNCT", "("):
            expr = self._expression()
            self._expect("PUNCT", ")")
            return expr
        if self._check("PUNCT", "["):
            return self._array()
        if self._check("PUNCT", "{"):
            return self._object()
        raise JSError(f"unexpected token {t.value!r}")

    def _array(self):
        self._expect("PUNCT", "[")
        items = []
        if not self._check("PUNCT", "]"):
            while True:
                items.append(self._assignment())
                if not self._match("PUNCT", ","):
                    break
        self._expect("PUNCT", "]")
        return ("array", items)

    def _object(self):
        self._expect("PUNCT", "{")
        pairs = []
        if not self._check("PUNCT", "}"):
            while True:
                key_tok = self._advance()
                key = str(key_tok.value)
                self._expect("PUNCT", ":")
                value = self._assignment()
                pairs.append((key, value))
                if not self._match("PUNCT", ","):
                    break
        self._expect("PUNCT", "}")
        return ("object", pairs)


# ---------------------------------------------------------------------------
# Runtime values
# ---------------------------------------------------------------------------


class JSFunction:
    def __init__(self, params, body, closure, interp) -> None:
        self.params = params
        self.body = body
        self.closure = closure
        self.interp = interp

    def call(self, args, this=UNDEFINED):
        scope = Scope(self.closure)
        scope.declare("this", this)
        for idx, name in enumerate(self.params):
            scope.declare(name, args[idx] if idx < len(args) else UNDEFINED)
        try:
            self.interp._exec_block(self.body[1], scope)
        except _Return as r:
            return r.value
        return UNDEFINED


class Scope:
    def __init__(self, parent: Optional["Scope"] = None) -> None:
        self.vars: Dict[str, Any] = {}
        self.parent = parent

    def declare(self, name: str, value: Any) -> None:
        self.vars[name] = value

    def get(self, name: str) -> Any:
        s: Optional[Scope] = self
        while s is not None:
            if name in s.vars:
                return s.vars[name]
            s = s.parent
        raise JSError(f"{name} is not defined")

    def has(self, name: str) -> bool:
        s: Optional[Scope] = self
        while s is not None:
            if name in s.vars:
                return True
            s = s.parent
        return False

    def set(self, name: str, value: Any) -> None:
        s: Optional[Scope] = self
        while s is not None:
            if name in s.vars:
                s.vars[name] = value
                return
            s = s.parent
        self.vars[name] = value  # implicit global


# ---------------------------------------------------------------------------
# Interpreter
# ---------------------------------------------------------------------------


class Interpreter:
    def __init__(self, globals_: Optional[Dict[str, Any]] = None) -> None:
        self.global_scope = Scope()
        self.console_output: List[str] = []
        self._install_builtins()
        if globals_:
            for k, v in globals_.items():
                self.global_scope.declare(k, v)

    def _install_builtins(self) -> None:
        from .js_dom import make_console, make_math, make_json

        self.global_scope.declare("console", make_console(self))
        self.global_scope.declare("Math", make_math())
        self.global_scope.declare("JSON", make_json())
        self.global_scope.declare("undefined", UNDEFINED)
        self.global_scope.declare("NaN", float("nan"))
        self.global_scope.declare("parseInt", _bi(lambda a: _parse_int(a)))
        self.global_scope.declare("parseFloat", _bi(lambda a: _parse_float(a)))
        self.global_scope.declare("String", _bi(lambda a=UNDEFINED: to_str(a)))
        self.global_scope.declare("Number", _bi(lambda a=UNDEFINED: to_num(a)))
        self.global_scope.declare("Boolean", _bi(lambda a=UNDEFINED: truthy(a)))
        self.global_scope.declare("Array", _bi(lambda *a: list(a)))

    def run(self, source: str) -> Any:
        tokens = Lexer(source).tokens()
        program = Parser(tokens).parse()
        # Hoist function declarations so they can be called before definition.
        for stmt in program:
            if stmt[0] == "funcdecl":
                _, name, params, body = stmt
                self.global_scope.declare(
                    name, JSFunction(params, body, self.global_scope, self))
        result = UNDEFINED
        for stmt in program:
            result = self._exec(stmt, self.global_scope)
        return result

    # -- execution --
    def _exec_block(self, stmts, scope) -> Any:
        result = UNDEFINED
        for stmt in stmts:
            result = self._exec(stmt, scope)
        return result

    def _exec(self, node, scope) -> Any:
        kind = node[0]
        if kind == "expr":
            return self._eval(node[1], scope)
        if kind == "var":
            for name, init in node[1]:
                scope.declare(name, self._eval(init, scope) if init else UNDEFINED)
            return UNDEFINED
        if kind == "funcdecl":
            _, name, params, body = node
            scope.declare(name, JSFunction(params, body, scope, self))
            return UNDEFINED
        if kind == "block":
            return self._exec_block(node[1], Scope(scope))
        if kind == "if":
            if truthy(self._eval(node[1], scope)):
                return self._exec(node[2], scope)
            elif node[3] is not None:
                return self._exec(node[3], scope)
            return UNDEFINED
        if kind == "while":
            while truthy(self._eval(node[1], scope)):
                try:
                    self._exec(node[2], scope)
                except _Break:
                    break
                except _Continue:
                    continue
            return UNDEFINED
        if kind == "for":
            _, init, cond, update, body = node
            loop_scope = Scope(scope)
            if init is not None:
                self._exec(init, loop_scope)
            while cond is None or truthy(self._eval(cond, loop_scope)):
                try:
                    self._exec(body, loop_scope)
                except _Break:
                    break
                except _Continue:
                    pass
                if update is not None:
                    self._eval(update, loop_scope)
            return UNDEFINED
        if kind == "return":
            raise _Return(self._eval(node[1], scope) if node[1] else UNDEFINED)
        if kind == "break":
            raise _Break()
        if kind == "continue":
            raise _Continue()
        raise JSError(f"cannot execute {kind}")

    # -- evaluation --
    def _eval(self, node, scope) -> Any:
        if node is None:
            return UNDEFINED
        kind = node[0]
        if kind == "__lit":
            return node[1]
        if kind == "num":
            return node[1]
        if kind == "str":
            return node[1]
        if kind == "bool":
            return node[1]
        if kind == "null":
            return None
        if kind == "undef":
            return UNDEFINED
        if kind == "ident":
            return scope.get(node[1])
        if kind == "array":
            return [self._eval(item, scope) for item in node[1]]
        if kind == "object":
            return {k: self._eval(v, scope) for k, v in node[1]}
        if kind == "funcexpr":
            return JSFunction(node[1], node[2], scope, self)
        if kind == "binary":
            return self._binary(node[1], node[2], node[3], scope)
        if kind == "unary":
            return self._unary(node[1], node[2], scope)
        if kind == "typeof":
            return _typeof(self._eval(node[1], scope))
        if kind == "ternary":
            return (self._eval(node[2], scope) if truthy(self._eval(node[1], scope))
                    else self._eval(node[3], scope))
        if kind == "assign":
            return self._assign(node[1], node[2], node[3], scope)
        if kind == "member":
            obj = self._eval(node[1], scope)
            return get_member(obj, node[2])
        if kind == "index":
            obj = self._eval(node[1], scope)
            key = self._eval(node[2], scope)
            return get_index(obj, key)
        if kind == "call":
            return self._call(node[1], node[2], scope)
        if kind in ("preupdate", "postupdate"):
            return self._update(node, scope)
        if kind == "new":
            return self._eval(node[1], scope)  # constructors approximated as calls
        raise JSError(f"cannot evaluate {kind}")

    def _binary(self, op, left_node, right_node, scope):
        if op == "&&":
            left = self._eval(left_node, scope)
            return self._eval(right_node, scope) if truthy(left) else left
        if op == "||":
            left = self._eval(left_node, scope)
            return left if truthy(left) else self._eval(right_node, scope)
        a = self._eval(left_node, scope)
        b = self._eval(right_node, scope)
        if op == "+":
            if isinstance(a, str) or isinstance(b, str):
                return to_str(a) + to_str(b)
            return to_num(a) + to_num(b)
        if op == "-":
            return to_num(a) - to_num(b)
        if op == "*":
            return to_num(a) * to_num(b)
        if op == "/":
            try:
                return to_num(a) / to_num(b)
            except ZeroDivisionError:
                return float("inf")
        if op == "%":
            try:
                return to_num(a) % to_num(b)
            except ZeroDivisionError:
                return float("nan")
        if op == "===":
            return strict_eq(a, b)
        if op == "!==":
            return not strict_eq(a, b)
        if op == "==":
            return loose_eq(a, b)
        if op == "!=":
            return not loose_eq(a, b)
        if op in ("<", ">", "<=", ">="):
            return _compare(op, a, b)
        raise JSError(f"bad operator {op}")

    def _unary(self, op, operand_node, scope):
        v = self._eval(operand_node, scope)
        if op == "-":
            return -to_num(v)
        if op == "+":
            return to_num(v)
        if op == "!":
            return not truthy(v)
        raise JSError(f"bad unary {op}")

    def _assign(self, op, target, value_node, scope):
        value = self._eval(value_node, scope)
        if op != "=":
            current = self._eval(target, scope)
            base = op[0]
            value = self._binary(base, ("__lit", current), ("__lit", value),
                                 scope)
        self._store(target, value, scope)
        return value

    def _store(self, target, value, scope):
        if target[0] == "ident":
            scope.set(target[1], value)
        elif target[0] == "member":
            obj = self._eval(target[1], scope)
            set_member(obj, target[2], value)
        elif target[0] == "index":
            obj = self._eval(target[1], scope)
            key = self._eval(target[2], scope)
            set_index(obj, key, value)
        else:
            raise JSError("invalid assignment target")

    def _update(self, node, scope):
        kind, op, target = node
        old = to_num(self._eval(target, scope))
        new = old + (1 if op == "++" else -1)
        self._store(target, new, scope)
        return new if kind == "preupdate" else old

    def _call(self, callee_node, arg_nodes, scope):
        args = [self._eval(a, scope) for a in arg_nodes]
        if callee_node[0] == "member":
            this = self._eval(callee_node[1], scope)
            fn = get_member(this, callee_node[2])
            return invoke(fn, args, this)
        fn = self._eval(callee_node, scope)
        return invoke(fn, args)


# ---------------------------------------------------------------------------
# Coercions and member access helpers
# ---------------------------------------------------------------------------


def _bi(fn):
    """Wrap a Python callable as a JS-callable builtin."""
    return ("builtin", fn)


def invoke(fn, args, this=UNDEFINED):
    if isinstance(fn, JSFunction):
        return fn.call(args, this)
    if isinstance(fn, tuple) and fn and fn[0] == "builtin":
        return fn[1](*args)
    if callable(fn):
        return fn(*args)
    raise JSError("value is not a function")


def truthy(v) -> bool:
    if v is UNDEFINED or v is None or v is False:
        return False
    if v is True:
        return True
    if isinstance(v, float):
        return v != 0 and v == v  # not zero, not NaN
    if isinstance(v, str):
        return len(v) > 0
    return True


def to_num(v) -> float:
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, float):
        return v
    if isinstance(v, int):
        return float(v)
    if v is None:
        return 0.0
    if v is UNDEFINED:
        return float("nan")
    if isinstance(v, str):
        try:
            return float(v.strip() or 0)
        except ValueError:
            return float("nan")
    return float("nan")


def to_str(v) -> str:
    if v is UNDEFINED:
        return "undefined"
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, float):
        if v != v:
            return "NaN"
        if v == int(v) and abs(v) < 1e16:
            return str(int(v))
        return repr(v)
    if isinstance(v, list):
        return ",".join(to_str(x) for x in v)
    if isinstance(v, dict):
        return "[object Object]"
    if isinstance(v, str):
        return v
    from .js_dom import ElementWrapper
    if isinstance(v, ElementWrapper):
        return f"[object HTML{v.node.tag.capitalize()}Element]"
    if isinstance(v, JSFunction) or (isinstance(v, tuple) and v and v[0] == "builtin"):
        return "function"
    return str(v)


def _typeof(v) -> str:
    if v is UNDEFINED:
        return "undefined"
    if v is None:
        return "object"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, float):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, JSFunction) or (isinstance(v, tuple) and v and v[0] == "builtin"):
        return "function"
    return "object"


def strict_eq(a, b) -> bool:
    if type(a) is bool or type(b) is bool:
        return a is b
    if isinstance(a, float) and isinstance(b, float):
        return a == b
    return a is b if (a is None or a is UNDEFINED
                      or b is None or b is UNDEFINED) else a == b


def loose_eq(a, b) -> bool:
    if (a is None or a is UNDEFINED) and (b is None or b is UNDEFINED):
        return True
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    if isinstance(a, (float, bool)) or isinstance(b, (float, bool)):
        try:
            return to_num(a) == to_num(b)
        except (ValueError, TypeError):
            return False
    return strict_eq(a, b)


def _compare(op, a, b):
    if isinstance(a, str) and isinstance(b, str):
        x, y = a, b
    else:
        x, y = to_num(a), to_num(b)
        if x != x or y != y:
            return False
    return {"<": x < y, ">": x > y, "<=": x <= y, ">=": x >= y}[op]


def _parse_int(v, *_):
    s = to_str(v).strip()
    num = ""
    for i, c in enumerate(s):
        if (c.isdigit() or (i == 0 and c in "+-")):
            num += c
        else:
            break
    try:
        return float(int(num))
    except ValueError:
        return float("nan")


def _parse_float(v, *_):
    s = to_str(v).strip()
    end = 0
    seen_dot = False
    for i, c in enumerate(s):
        if c.isdigit() or (i == 0 and c in "+-"):
            end = i + 1
        elif c == "." and not seen_dot:
            seen_dot = True
            end = i + 1
        else:
            break
    try:
        return float(s[:end])
    except ValueError:
        return float("nan")


def get_member(obj, name):
    from .js_dom import host_get
    if isinstance(obj, str):
        return _string_member(obj, name)
    if isinstance(obj, list):
        return _array_member(obj, name)
    if isinstance(obj, dict):
        return obj.get(name, UNDEFINED)
    if obj is None or obj is UNDEFINED:
        raise JSError(f"cannot read '{name}' of {to_str(obj)}")
    return host_get(obj, name)


def set_member(obj, name, value):
    from .js_dom import host_set
    if isinstance(obj, dict):
        obj[name] = value
        return
    if obj is None or obj is UNDEFINED:
        raise JSError(f"cannot set '{name}' of {to_str(obj)}")
    host_set(obj, name, value)


def get_index(obj, key):
    if isinstance(obj, list):
        i = int(to_num(key))
        if 0 <= i < len(obj):
            return obj[i]
        if isinstance(key, str):
            return _array_member(obj, key)
        return UNDEFINED
    if isinstance(obj, str):
        i = int(to_num(key))
        return obj[i] if 0 <= i < len(obj) else UNDEFINED
    if isinstance(obj, dict):
        return obj.get(to_str(key), UNDEFINED)
    return get_member(obj, to_str(key))


def set_index(obj, key, value):
    if isinstance(obj, list):
        i = int(to_num(key))
        while len(obj) <= i:
            obj.append(UNDEFINED)
        obj[i] = value
        return
    if isinstance(obj, dict):
        obj[to_str(key)] = value
        return
    set_member(obj, to_str(key), value)


def _string_member(s: str, name: str):
    if name == "length":
        return float(len(s))
    methods = {
        "toUpperCase": lambda: s.upper(),
        "toLowerCase": lambda: s.lower(),
        "trim": lambda: s.strip(),
        "charAt": lambda i=0: s[int(to_num(i))] if 0 <= int(to_num(i)) < len(s) else "",
        "indexOf": lambda sub="": float(s.find(to_str(sub))),
        "includes": lambda sub="": to_str(sub) in s,
        "startsWith": lambda p="": s.startswith(to_str(p)),
        "endsWith": lambda p="": s.endswith(to_str(p)),
        "slice": lambda a=0, b=None: s[int(to_num(a)):(int(to_num(b)) if b is not None else len(s))],
        "substring": lambda a=0, b=None: s[int(to_num(a)):(int(to_num(b)) if b is not None else len(s))],
        "split": lambda sep=None: list(s) if sep in (None, UNDEFINED) else s.split(to_str(sep)),
        "replace": lambda a="", b="": s.replace(to_str(a), to_str(b), 1),
        "repeat": lambda n=0: s * int(to_num(n)),
        "concat": lambda *rest: s + "".join(to_str(x) for x in rest),
    }
    if name in methods:
        return _bi(methods[name])
    if name.lstrip("-").isdigit():
        i = int(name)
        return s[i] if 0 <= i < len(s) else UNDEFINED
    return UNDEFINED


def _array_member(arr: list, name: str):
    if name == "length":
        return float(len(arr))
    methods = {
        "push": lambda *xs: (arr.extend(xs), float(len(arr)))[1],
        "pop": lambda: arr.pop() if arr else UNDEFINED,
        "shift": lambda: arr.pop(0) if arr else UNDEFINED,
        "unshift": lambda *xs: (arr.__setitem__(slice(0, 0), list(xs)), float(len(arr)))[1],
        "join": lambda sep=",": to_str(sep).join(to_str(x) for x in arr),
        "indexOf": lambda x: float(arr.index(x)) if x in arr else -1.0,
        "includes": lambda x: x in arr,
        "slice": lambda a=0, b=None: arr[int(to_num(a)):(int(to_num(b)) if b is not None else len(arr))],
        "reverse": lambda: (arr.reverse(), arr)[1],
        "concat": lambda *xs: arr + [i for x in xs for i in (x if isinstance(x, list) else [x])],
        "map": lambda fn: [invoke(fn, [v, float(i)]) for i, v in enumerate(arr)],
        "filter": lambda fn: [v for i, v in enumerate(arr) if truthy(invoke(fn, [v, float(i)]))],
        "forEach": lambda fn: ([invoke(fn, [v, float(i)]) for i, v in enumerate(arr)], UNDEFINED)[1],
    }
    if name in methods:
        return _bi(methods[name])
    return UNDEFINED
