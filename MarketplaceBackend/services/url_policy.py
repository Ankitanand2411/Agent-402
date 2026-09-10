"""
Outbound URL policy for proxy tools (SSRF defence).

A provider registers a `targetUrl` that this server will POST to on every paid
call. Without checks, that URL can point at things only the server can reach:
the cloud metadata endpoint (169.254.169.254), localhost (including this
service's own admin routes), or private-network hosts. That is server-side
request forgery, and it is the single most common way an "innocent" proxy
feature becomes a breach.

Policy
  * scheme must be https (http allowed only when ALLOW_INSECURE_TOOL_URLS=true, for local dev)
  * no credentials in the URL, no empty host
  * the host must not be a known local name (localhost, *.localhost, *.internal, *.local)
  * every address the host resolves to must be a public unicast IP: no loopback,
    private, link-local, multicast, reserved, unspecified, or unique-local ranges

Validate at registration and approval (fail fast, tell the provider) AND right
before each outbound call (a hostname's records can change after approval:
DNS rebinding). Redirects are never followed, so a public URL cannot bounce the
request to a private one.
"""

import ipaddress
import socket
from urllib.parse import urlsplit

from config import settings

BLOCKED_HOST_SUFFIXES = (".localhost", ".internal", ".local", ".localdomain")
BLOCKED_HOSTS = {"localhost", "metadata", "metadata.google.internal", "instance-data"}


class UnsafeURL(ValueError):
    """The URL points somewhere this server must not call."""


def _is_public(ip: ipaddress._BaseAddress) -> bool:
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified
        or (isinstance(ip, ipaddress.IPv6Address) and (ip.is_site_local or ip.ipv4_mapped is not None and not _is_public(ip.ipv4_mapped)))
    )


def resolve(host: str) -> list[ipaddress._BaseAddress]:
    """All A/AAAA records for host (or the literal IP). Raises UnsafeURL if it does not resolve."""
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
        return [literal]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise UnsafeURL(f"host {host!r} does not resolve") from e
    addrs = {ipaddress.ip_address(info[4][0]) for info in infos}
    if not addrs:
        raise UnsafeURL(f"host {host!r} does not resolve")
    return sorted(addrs, key=str)


def validate_target_url(url: str) -> str:
    """Return the URL if it may be called; raise UnsafeURL otherwise."""
    if not url or not isinstance(url, str):
        raise UnsafeURL("targetUrl is required")
    parts = urlsplit(url.strip())
    allowed_schemes = {"https"} | ({"http"} if settings.ALLOW_INSECURE_TOOL_URLS else set())
    if parts.scheme not in allowed_schemes:
        raise UnsafeURL(f"scheme must be one of {sorted(allowed_schemes)}")
    if parts.username or parts.password:
        raise UnsafeURL("credentials in targetUrl are not allowed")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise UnsafeURL("targetUrl has no host")
    if host in BLOCKED_HOSTS or host.endswith(BLOCKED_HOST_SUFFIXES):
        raise UnsafeURL(f"host {host!r} is not allowed")
    for ip in resolve(host):
        if not _is_public(ip):
            raise UnsafeURL(f"host {host!r} resolves to non-public address {ip}")
    return url.strip()
