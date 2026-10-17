"""PyInstaller runtime hook used only by the exact-artifact release smoke.

The hook is inert for ordinary launches.  The release harness enables it with three
private environment variables, after which the frozen process may use literal loopback
sockets but any DNS or non-loopback socket attempt fails before application imports.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import sys
from pathlib import Path

_ENABLE_ENV = "ADLIFE_FROZEN_SMOKE_NETWORK_GUARD"
_RECEIPT_ENV = "ADLIFE_FROZEN_SMOKE_GUARD_RECEIPT"
_TOKEN_ENV = "ADLIFE_FROZEN_SMOKE_GUARD_TOKEN"
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _loopback_ip(value: object) -> bool:
    if isinstance(value, bytes):
        try:
            value = value.decode("ascii", errors="strict")
        except UnicodeDecodeError:
            return False
    if not isinstance(value, str):
        return False
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _local_socket_address(address: object) -> bool:
    # Python represents AF_UNIX addresses as str/bytes and TCP addresses as tuples.
    if isinstance(address, (str, bytes)):
        return True
    return isinstance(address, tuple) and bool(address) and _loopback_ip(address[0])


def _deny_external_network(event: str, arguments: tuple[object, ...]) -> None:
    if event == "socket.getaddrinfo":
        host = arguments[0] if arguments else None
        if _loopback_ip(host):
            return
        raise RuntimeError("frozen smoke blocked external network resolution")
    if event in {
        "socket.gethostbyname",
        "socket.gethostbyname_ex",
        "socket.gethostbyaddr",
        "socket.getnameinfo",
    }:
        raise RuntimeError("frozen smoke blocked external network resolution")
    if event in {"socket.connect", "socket.sendto", "socket.sendmsg"}:
        address = arguments[1] if len(arguments) > 1 else None
        if _local_socket_address(address):
            return
        raise RuntimeError("frozen smoke blocked external network operation")


def _write_activation_receipt(path: Path, *, token: str) -> None:
    if not path.is_absolute() or not path.parent.is_dir() or path.parent.is_symlink():
        raise RuntimeError("frozen smoke network-guard receipt path is unsafe")
    payload = (
        json.dumps(
            {
                "schema_version": 1,
                "external_network_denied": True,
                "pid": os.getpid(),
                "token": token,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags | no_follow, 0o600)
    try:
        written = os.write(descriptor, payload)
        if written != len(payload):
            raise RuntimeError("frozen smoke network-guard receipt was not fully written")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    if path.is_symlink() or path.read_bytes() != payload:
        raise RuntimeError("frozen smoke network-guard receipt verification failed")


def _activate() -> None:
    receipt_text = os.environ.pop(_RECEIPT_ENV, "")
    token = os.environ.pop(_TOKEN_ENV, "")
    os.environ.pop(_ENABLE_ENV, None)
    if not receipt_text or _TOKEN.fullmatch(token) is None:
        raise RuntimeError("frozen smoke network-guard configuration is invalid")

    sys.addaudithook(_deny_external_network)
    sys.audit("socket.connect", None, ("127.0.0.1", 1))
    try:
        sys.audit("socket.connect", None, ("203.0.113.1", 443))
    except RuntimeError:
        pass
    else:
        raise RuntimeError("frozen smoke external-network self-test failed")
    _write_activation_receipt(Path(receipt_text), token=token)


if os.environ.get(_ENABLE_ENV) == "1":
    _activate()
