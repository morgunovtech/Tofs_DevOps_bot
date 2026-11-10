"""Where the bot may connect when the address came from someone else.

A monitor the owner typed is checked where it points — a NAS in the LAN is
a legitimate monitor (ALLOW_PRIVATE_TARGETS=0 forbids even that). But the
links on a page, nested sitemaps, the redirects of a public site and an
image URL handed back by the screenshot service are written by other people:
those may lead only to the public internet, never to 127.0.0.1, the LAN,
169.254.169.254 or the bot's own :8080. The check runs on the RESOLVED
address, so names, hex forms (0x7f.1), redirects and DNS rebinding are
covered in one place — except the monitor's own address: with
ALLOW_PRIVATE_TARGETS=1 (the default) the monitor's name, and the names of
its own domain (www.example.com for example.com — a home router answers
them with the LAN address, «split horizon»), may point inside, on the
monitor's port only; ALLOW_PRIVATE_TARGETS=0 closes that too.

The same predicates decide what is «internal» everywhere in the project:
what is never sent to check-host, what ALLOW_PRIVATE_TARGETS=0 refuses at
input, where a session may connect."""

import errno
import ipaddress
import socket
from urllib.parse import urlparse

import aiohttp

from config import config
from utils.urls import host_of, registrable_domain

PRIVATE_REFUSED = "адрес во внутренней сети — туда не хожу"
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
# Zones that exist only inside a LAN (mDNS, RFC 8375, the usual corporate
# and home-router names) — one list for every «is it internal» question.
_LAN_SUFFIXES = (".local", ".localhost", ".localdomain", ".lan", ".home", ".home.arpa",
                 ".internal", ".intranet", ".corp")
_DEFAULT_PORTS = {"http": 80, "https": 443}


def is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Globally routable, not reserved, not multicast; an IPv4 address
    wrapped into IPv6 (::ffff:…, 6to4, Teredo, NAT64) is judged by itself."""
    if ip.version == 6:
        if ip in _NAT64:
            return is_public_ip(ipaddress.IPv4Address(int(ip) & 0xFFFF_FFFF))
        inner = ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None)
        if inner is not None:
            return is_public_ip(inner)
    return ip.is_global and not ip.is_reserved and not ip.is_multicast


def _parse_ip(text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        return None


def normalize_ipv4(host: str) -> str | None:
    """'0x7f.1', '127.1', '0177.0.0.1', '2130706433' → '127.0.0.1'; None when
    the host is not an IPv4 address in any of the forms the C library accepts."""
    if not host or not all(c in "0123456789abcdefx." for c in host.lower()) or host.endswith("."):
        return None
    try:
        return socket.inet_ntoa(socket.inet_aton(host))
    except OSError:
        return None


def is_lan_host(host: str) -> bool:
    """The owner typed an internal address: an IP outside the public
    internet (in any spelling), or a name that exists only inside a LAN."""
    host = (host or "").lower().rstrip(".").strip("[]")
    host = normalize_ipv4(host) or host
    ip = _parse_ip(host)
    if ip is not None:
        return not is_public_ip(ip)
    return "." not in host or host.endswith(_LAN_SUFFIXES)


def is_private_target(url: str) -> bool:
    """The address names something inside a LAN (is_lan_host of its host);
    an address without a host is not a public one either."""
    host = host_of(url)
    return not host or is_lan_host(host)


def _same_domain(a: str, b: str) -> bool:
    """One site however it is spelled (example.com and www.example.com);
    an IP literal is only itself."""
    if a == b:
        return True
    if _parse_ip(a) is not None or _parse_ip(b) is not None:
        return False
    return registrable_domain(a) == registrable_domain(b)


class PrivateAddressError(OSError):
    def __init__(self, host: str):
        super().__init__(errno.EACCES, PRIVATE_REFUSED)
        self.host = host


class GuardedConnector(aiohttp.TCPConnector):
    """TCPConnector that connects only to public addresses — except:
      * the host the owner configured (`trusted`) and the hosts of its own
        registrable domain, on the monitor's port only (a redirect of the
        monitor's name to another port of the bot's host is not followed
        inside) — while ALLOW_PRIVATE_TARGETS=1;
      * when that host is a LAN one, the rest of the LAN too (a NAS
        redirecting to its own IP).

    The hook is aiohttp's internal `_resolve_host` (aiohttp==3.14.5 in
    requirements.txt): it is called for IP literals and names alike and on
    every redirect hop. tests/test_outbound_policy.py fails loudly when an
    upgrade stops calling it."""

    def __init__(self, *args, trusted: str = "", trusted_port: int | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._trusted = (trusted or "").lower().rstrip(".")
        # The monitor's own port; the two standard web ports are the same
        # server however the redirect spells http:// and https://.
        self._trusted_ports = {p for p in (trusted_port, 80, 443) if p}
        self._lan = bool(self._trusted) and config.allow_private_targets and is_lan_host(self._trusted)

    def _trusts(self, host: str, port: int) -> bool:
        if self._lan:
            return True
        if not self._trusted or not config.allow_private_targets:
            return False
        return _same_domain(host.lower().rstrip("."), self._trusted) and port in self._trusted_ports

    async def _resolve_host(self, host, port, traces=None):
        infos = await super()._resolve_host(host, port, traces)
        if self._trusts(host, port):
            return infos
        public = [i for i in infos if (ip := _parse_ip(i["host"])) is not None and is_public_ip(ip)]
        if not public:
            raise PrivateAddressError(host)
        return public


def owner_connector(url: str, **kwargs) -> GuardedConnector:
    """For a session that starts at an address the owner configured (a
    monitor, the screenshot service) and then follows other people's links."""
    return GuardedConnector(trusted=host_of(url), trusted_port=_port_of(url), **kwargs)


def _port_of(url: str) -> int | None:
    """The port the URL connects to: explicit, or the scheme's default."""
    try:
        p = urlparse(url)
        return p.port or _DEFAULT_PORTS.get(p.scheme)
    except ValueError:
        return None


def public_connector(**kwargs) -> GuardedConnector:
    return GuardedConnector(**kwargs)


def refused_private(exc: BaseException) -> bool:
    """The request failed because the address is internal (not a network error)."""
    return isinstance(getattr(exc, "os_error", None), PrivateAddressError) or isinstance(exc, PrivateAddressError)
