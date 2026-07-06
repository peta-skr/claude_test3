"""A minimal-but-real network stack.

Everything here is built directly on ``socket`` and ``ssl`` from the
standard library -- there is no ``requests``/``urllib`` involved in the
request path.  Supported schemes:

* ``http``  / ``https`` -- real TCP(+TLS) requests, redirects, gzip, chunked
* ``file``              -- read a local file
* ``data``              -- inline ``data:text/html,...`` documents
* ``about``             -- built-in pages such as ``about:blank``

The public entry point is :meth:`URL.request`, which returns
``(headers, body)``.
"""

from __future__ import annotations

import gzip
import os
import socket
import ssl
from typing import Dict, Optional, Tuple
from urllib.parse import unquote

DEFAULT_PORTS = {"http": 80, "https": 443}
MAX_REDIRECTS = 10
USER_AGENT = "pybrowser/0.1 (from-scratch; +https://example.invalid)"


def _proxy_for(scheme: str) -> Optional[Tuple[str, int]]:
    """Return ``(host, port)`` of the configured proxy for ``scheme``, if any.

    Honours the conventional ``HTTPS_PROXY`` / ``HTTP_PROXY`` environment
    variables so the browser works behind a corporate/egress proxy just like
    a real one.
    """
    value = os.environ.get(f"{scheme.upper()}_PROXY") or os.environ.get(
        f"{scheme}_proxy"
    )
    if not value:
        return None
    if "://" in value:
        value = value.split("://", 1)[1]
    value = value.rstrip("/")
    if ":" in value:
        host, port = value.rsplit(":", 1)
        try:
            return host, int(port)
        except ValueError:
            return host, 8080
    return value, 8080


def _make_ssl_context() -> ssl.SSLContext:
    """Build a TLS context that trusts the system CAs plus any bundle named
    in the standard CA environment variables (so a re-terminating proxy's CA
    is honoured without disabling verification)."""
    ctx = ssl.create_default_context()
    for var in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        path = os.environ.get(var)
        if path and os.path.exists(path):
            try:
                ctx.load_verify_locations(cafile=path)
            except (ssl.SSLError, OSError):
                pass
    return ctx


class URLError(Exception):
    """Raised when a URL cannot be fetched."""


class URL:
    """Parse a URL string and fetch it."""

    def __init__(self, url: str) -> None:
        self.raw = url
        self.scheme = "about"
        self.host = ""
        self.port = 0
        self.path = ""
        self.is_data = False
        self.data_mime = "text/html"
        self.data_content = ""
        self._parse(url)

    # -- parsing -------------------------------------------------------

    def _parse(self, url: str) -> None:
        if url.startswith("data:"):
            self.scheme = "data"
            self.is_data = True
            rest = url[len("data:"):]
            meta, _, content = rest.partition(",")
            if ";base64" in meta:
                # base64 payloads are decoded lazily in request().
                self.data_mime = meta.replace(";base64", "") or "text/plain"
                self.data_base64 = True
            else:
                self.data_mime = meta or "text/plain"
                self.data_base64 = False
            self.data_content = content
            return

        if url.startswith("about:"):
            self.scheme = "about"
            self.path = url[len("about:"):]
            return

        if "://" not in url:
            # Bare host or path: assume http, or treat as about page.
            if "." in url.split("/")[0]:
                url = "http://" + url
            else:
                self.scheme = "about"
                self.path = url
                return

        self.scheme, rest = url.split("://", 1)
        self.scheme = self.scheme.lower()

        if self.scheme == "file":
            self.path = rest
            return

        if self.scheme not in DEFAULT_PORTS:
            raise URLError(f"unsupported scheme: {self.scheme!r}")

        if "/" not in rest:
            rest = rest + "/"
        authority, path = rest.split("/", 1)
        self.path = "/" + path

        self.port = DEFAULT_PORTS[self.scheme]
        # Strip any userinfo@ prefix.
        if "@" in authority:
            authority = authority.rsplit("@", 1)[1]
        if ":" in authority:
            self.host, port_str = authority.rsplit(":", 1)
            try:
                self.port = int(port_str)
            except ValueError:
                pass
        else:
            self.host = authority

    # -- fetching ------------------------------------------------------

    def request(self, timeout: float = 20.0) -> Tuple[Dict[str, str], str]:
        """Fetch the resource, following redirects. Returns (headers, body)."""
        if self.scheme == "data":
            return self._request_data()
        if self.scheme == "about":
            return self._request_about()
        if self.scheme == "file":
            return self._request_file()
        return self._request_http(timeout, redirects_left=MAX_REDIRECTS)

    def _request_data(self) -> Tuple[Dict[str, str], str]:
        content = self.data_content
        if getattr(self, "data_base64", False):
            import base64

            body = base64.b64decode(content).decode("utf-8", "replace")
        else:
            body = unquote(content)
        return {"content-type": self.data_mime}, body

    def _request_about(self) -> Tuple[Dict[str, str], str]:
        from .about import about_page

        return {"content-type": "text/html"}, about_page(self.path or "blank")

    def _request_file(self) -> Tuple[Dict[str, str], str]:
        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as f:
                return {"content-type": "text/html"}, f.read()
        except OSError as exc:
            raise URLError(f"cannot open file {self.path!r}: {exc}") from exc

    def _request_http(
        self, timeout: float, redirects_left: int
    ) -> Tuple[Dict[str, str], str]:
        raw = self._open_and_send(timeout)
        headers, body_bytes = self._read_response(raw)

        status = raw.status_code
        if status in (301, 302, 303, 307, 308) and "location" in headers:
            if redirects_left <= 0:
                raise URLError("too many redirects")
            location = headers["location"]
            target = self.resolve(location)
            return target._request_http(timeout, redirects_left - 1)

        body = self._decode_body(headers, body_bytes)
        return headers, body

    def _open_and_send(self, timeout: float) -> "_RawResponse":
        proxy = _proxy_for(self.scheme)
        s = socket.socket(
            family=socket.AF_INET, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
        )
        s.settimeout(timeout)

        request_target = self.path
        if proxy is not None:
            s.connect(proxy)
            if self.scheme == "https":
                # Tunnel through the proxy with CONNECT, then start TLS.
                s = self._connect_tunnel(s)
            else:
                # Plain HTTP proxies want the absolute-form request target.
                request_target = str(self)
        else:
            s.connect((self.host, self.port))
            if self.scheme == "https":
                ctx = _make_ssl_context()
                s = ctx.wrap_socket(s, server_hostname=self.host)

        request_lines = [
            f"GET {request_target} HTTP/1.1",
            f"Host: {self.host}",
            "Connection: close",
            f"User-Agent: {USER_AGENT}",
            "Accept-Encoding: gzip",
            "Accept: text/html,application/xhtml+xml,*/*",
        ]
        request = "\r\n".join(request_lines) + "\r\n\r\n"
        s.send(request.encode("utf-8"))
        return _RawResponse(s)

    def _connect_tunnel(self, s: socket.socket) -> ssl.SSLSocket:
        """Establish an HTTP CONNECT tunnel and wrap it in TLS."""
        connect = (
            f"CONNECT {self.host}:{self.port} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Proxy-Connection: keep-alive\r\n\r\n"
        )
        s.send(connect.encode("ascii"))
        # Read the proxy's response to CONNECT (headers terminated by blank line).
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        status_line = buf.split(b"\r\n", 1)[0].decode("iso-8859-1", "replace")
        parts = status_line.split(" ", 2)
        if len(parts) < 2 or parts[1] != "200":
            raise URLError(f"proxy CONNECT failed: {status_line!r}")
        ctx = _make_ssl_context()
        return ctx.wrap_socket(s, server_hostname=self.host)

    def _read_response(self, raw: "_RawResponse") -> Tuple[Dict[str, str], bytes]:
        headers = raw.read_headers()
        if headers.get("transfer-encoding", "").lower() == "chunked":
            body = raw.read_chunked()
        else:
            length = headers.get("content-length")
            body = raw.read_body(int(length) if length else None)
        raw.close()
        return headers, body

    @staticmethod
    def _decode_body(headers: Dict[str, str], body_bytes: bytes) -> str:
        if headers.get("content-encoding", "").lower() == "gzip":
            try:
                body_bytes = gzip.decompress(body_bytes)
            except OSError:
                pass
        charset = "utf-8"
        ctype = headers.get("content-type", "")
        if "charset=" in ctype:
            charset = ctype.split("charset=", 1)[1].split(";")[0].strip() or "utf-8"
        try:
            return body_bytes.decode(charset, "replace")
        except (LookupError, ValueError):
            return body_bytes.decode("utf-8", "replace")

    # -- helpers -------------------------------------------------------

    def resolve(self, url: str) -> "URL":
        """Resolve ``url`` relative to this URL (for links and redirects)."""
        if "://" in url or url.startswith(("data:", "about:")):
            return URL(url)
        if url.startswith("//"):
            return URL(f"{self.scheme}:{url}")
        if not url.startswith("/"):
            # Relative path: strip the current file component and append.
            dir_path = self.path.rsplit("/", 1)[0]
            while url.startswith("../"):
                url = url[3:]
                dir_path = dir_path.rsplit("/", 1)[0] if "/" in dir_path else ""
            url = dir_path + "/" + url
        port_suffix = ""
        if self.port and self.port != DEFAULT_PORTS.get(self.scheme):
            port_suffix = f":{self.port}"
        return URL(f"{self.scheme}://{self.host}{port_suffix}{url}")

    def __str__(self) -> str:
        if self.scheme == "data":
            return self.raw
        if self.scheme == "about":
            return f"about:{self.path}"
        if self.scheme == "file":
            return f"file://{self.path}"
        port_suffix = ""
        if self.port and self.port != DEFAULT_PORTS.get(self.scheme):
            port_suffix = f":{self.port}"
        return f"{self.scheme}://{self.host}{port_suffix}{self.path}"


class _RawResponse:
    """Buffers a socket and parses the HTTP/1.x response off the wire."""

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.buffer = b""
        self.status_code = 0
        self.status_text = ""
        self._closed = False

    def _fill(self, size: int = 65536) -> bool:
        try:
            chunk = self.sock.recv(size)
        except (OSError, ssl.SSLError):
            return False
        if not chunk:
            return False
        self.buffer += chunk
        return True

    def _read_until(self, sep: bytes) -> Optional[bytes]:
        while sep not in self.buffer:
            if not self._fill():
                return None
        idx = self.buffer.index(sep)
        line = self.buffer[:idx]
        self.buffer = self.buffer[idx + len(sep):]
        return line

    def read_headers(self) -> Dict[str, str]:
        status_line = self._read_until(b"\r\n") or b""
        parts = status_line.decode("iso-8859-1").split(" ", 2)
        if len(parts) >= 2 and parts[1].isdigit():
            self.status_code = int(parts[1])
            self.status_text = parts[2] if len(parts) > 2 else ""
        headers: Dict[str, str] = {}
        while True:
            line = self._read_until(b"\r\n")
            if line is None or line == b"":
                break
            text = line.decode("iso-8859-1")
            if ":" in text:
                key, value = text.split(":", 1)
                headers[key.strip().lower()] = value.strip()
        return headers

    def read_body(self, length: Optional[int]) -> bytes:
        if length is not None:
            while len(self.buffer) < length:
                if not self._fill():
                    break
            body = self.buffer[:length]
            self.buffer = self.buffer[length:]
            return body
        # No content-length: read until the server closes the connection.
        while self._fill():
            pass
        body = self.buffer
        self.buffer = b""
        return body

    def read_chunked(self) -> bytes:
        body = b""
        while True:
            size_line = self._read_until(b"\r\n")
            if size_line is None:
                break
            size_str = size_line.split(b";", 1)[0].strip()
            try:
                size = int(size_str, 16)
            except ValueError:
                break
            if size == 0:
                self._read_until(b"\r\n")  # trailing CRLF
                break
            while len(self.buffer) < size + 2:
                if not self._fill():
                    break
            body += self.buffer[:size]
            self.buffer = self.buffer[size + 2:]  # drop chunk + CRLF
        return body

    def close(self) -> None:
        if not self._closed:
            try:
                self.sock.close()
            except OSError:
                pass
            self._closed = True
