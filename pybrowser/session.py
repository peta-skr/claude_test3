"""Per-session state shared across a browser's tabs: cookies and a cache.

* :class:`CookieJar` parses ``Set-Cookie`` response headers and replays the
  matching cookies on later requests to the same host/path (honouring
  ``Domain``, ``Path``, ``Secure`` and expiry).
* :class:`Cache` is a small in-memory store of GET responses keyed by URL.

A :class:`Session` bundles the two and is threaded through
:meth:`pybrowser.url.URL.request`.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple


class Cookie:
    __slots__ = ("name", "value", "domain", "path", "secure", "expires")

    def __init__(self, name, value, domain, path, secure, expires) -> None:
        self.name = name
        self.value = value
        self.domain = domain
        self.path = path
        self.secure = secure
        self.expires = expires  # epoch seconds, or None for session cookie

    def key(self):
        return (self.domain, self.path, self.name)


class CookieJar:
    def __init__(self) -> None:
        self.cookies: Dict[tuple, Cookie] = {}

    def set_from_headers(self, url, set_cookie_values, now: float = 0.0) -> None:
        for raw in set_cookie_values:
            cookie = self._parse(url, raw)
            if cookie is None:
                continue
            # Expiry in the past (and not a session cookie) deletes the cookie.
            if cookie.expires is not None and cookie.expires <= now:
                self.cookies.pop(cookie.key(), None)
            else:
                self.cookies[cookie.key()] = cookie

    def header_for(self, url, now: float = 0.0) -> Optional[str]:
        pairs: List[str] = []
        for cookie in self.cookies.values():
            if cookie.expires is not None and cookie.expires <= now:
                continue
            if cookie.secure and url.scheme != "https":
                continue
            if not _domain_match(url.host, cookie.domain):
                continue
            if not url.path.startswith(cookie.path):
                continue
            pairs.append(f"{cookie.name}={cookie.value}")
        return "; ".join(pairs) if pairs else None

    def _parse(self, url, raw: str) -> Optional[Cookie]:
        parts = [p.strip() for p in raw.split(";")]
        if not parts or "=" not in parts[0]:
            return None
        name, value = parts[0].split("=", 1)
        domain = url.host
        path = "/"
        secure = False
        expires = None
        for attr in parts[1:]:
            if "=" in attr:
                k, v = attr.split("=", 1)
                k = k.strip().lower()
                v = v.strip()
                if k == "domain":
                    domain = v.lstrip(".") or url.host
                elif k == "path":
                    path = v or "/"
                elif k == "max-age":
                    try:
                        expires = float(v)  # relative; treated as absolute here
                    except ValueError:
                        pass
            elif attr.lower() == "secure":
                secure = True
        return Cookie(name.strip(), value.strip(), domain, path, secure, expires)

    def __len__(self):
        return len(self.cookies)


def _domain_match(host: str, domain: str) -> bool:
    host = host.lower()
    domain = domain.lower()
    return host == domain or host.endswith("." + domain)


class Cache:
    """A tiny in-memory response cache keyed by URL string."""

    def __init__(self, max_entries: int = 64) -> None:
        self.max_entries = max_entries
        self._store: Dict[str, Tuple[Dict[str, str], bytes]] = {}
        self.hits = 0
        self.misses = 0

    def get(self, key: str):
        entry = self._store.get(key)
        if entry is not None:
            self.hits += 1
        else:
            self.misses += 1
        return entry

    def put(self, key: str, headers: Dict[str, str], body: bytes) -> None:
        if "no-store" in headers.get("cache-control", "").lower():
            return
        if len(self._store) >= self.max_entries:
            # Drop an arbitrary (oldest-inserted) entry.
            self._store.pop(next(iter(self._store)))
        self._store[key] = (dict(headers), body)

    def clear(self) -> None:
        self._store.clear()

    def __contains__(self, key):
        return key in self._store

    def __len__(self):
        return len(self._store)


class Session:
    """Cookies + cache shared by every tab in a browser window."""

    def __init__(self) -> None:
        self.cookies = CookieJar()
        self.cache = Cache()
