"""폰 앱 연결: PC 정보, LAN 자동 검색(UDP), PIN 페어링, 기기별 토큰."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import secrets
import socket
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from . import __version__

log = logging.getLogger(__name__)

DISCOVERY_PORT = 8766
DISCOVERY_MAGIC = b"ANIME_RECORDER_DISCOVER_V1"
TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")
PIN_TTL = 120  # 초
PIN_MAX_TRIES = 5


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def local_addresses() -> list[dict]:
    """이 PC의 IPv4 주소들. Tailscale 주소는 kind="tailscale"."""
    out = []
    try:
        import psutil

        for addrs in psutil.net_if_addrs().values():
            for a in addrs:
                if a.family != socket.AF_INET:
                    continue
                ip = ipaddress.ip_address(a.address)
                if ip.is_loopback or ip.is_link_local:
                    continue
                out.append({"ip": a.address, "kind": "tailscale" if ip in TAILSCALE_NET else "lan"})
    except Exception:  # noqa: BLE001 - 주소 목록은 부가 정보
        pass
    out.sort(key=lambda a: (a["kind"] != "lan", a["ip"]))
    return out


class HostInfo:
    def __init__(self, data_dir: Path, name: str, port: int):
        id_file = Path(data_dir) / "host_id"
        if not id_file.exists():
            id_file.write_text(uuid.uuid4().hex, encoding="utf-8")
        self.id = id_file.read_text(encoding="utf-8").strip()
        self.name = name or socket.gethostname()
        self.port = port

    def as_dict(self) -> dict:
        return {"app": "anime-recorder", "id": self.id, "name": self.name, "port": self.port,
                "version": __version__, "addresses": local_addresses()}


class Pairing:
    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "devices.json"
        self._lock = threading.Lock()
        self.devices: list[dict] = []
        if self.path.exists():
            self.devices = json.loads(self.path.read_text(encoding="utf-8"))
        self.pending: dict | None = None
        self.on_pin = lambda device, pin: None  # PC 화면에 PIN 표시

    def _save(self) -> None:
        self.path.write_text(json.dumps(self.devices, ensure_ascii=False, indent=2), encoding="utf-8")

    def start(self, device: str) -> str:
        device = (device or "휴대폰").strip()[:40]
        with self._lock:
            pin = f"{secrets.randbelow(10000):04d}"
            self.pending = {"id": uuid.uuid4().hex, "pin": pin, "device": device,
                            "expires": time.monotonic() + PIN_TTL, "tries": 0}
            pair_id = self.pending["id"]
        self.on_pin(device, pin)
        return pair_id

    def confirm(self, pair_id: str, pin: str) -> str:
        with self._lock:
            p = self.pending
            if not p or p["id"] != pair_id or time.monotonic() > p["expires"]:
                self.pending = None
                raise PermissionError("페어링 요청이 만료되었습니다. 다시 시도하세요")
            if not secrets.compare_digest(p["pin"], (pin or "").strip()):
                p["tries"] += 1
                if p["tries"] >= PIN_MAX_TRIES:
                    self.pending = None
                    raise PermissionError("PIN을 여러 번 틀려 요청이 취소되었습니다")
                raise PermissionError(f"PIN이 맞지 않습니다 ({PIN_MAX_TRIES - p['tries']}번 남음)")
            self.pending = None
            token = secrets.token_urlsafe(24)
            self.devices.append({"id": uuid.uuid4().hex[:8], "name": p["device"], "hash": _hash(token),
                                 "created": datetime.now().isoformat(timespec="seconds")})
            self._save()
            return token

    def verify(self, token: str) -> bool:
        if not token:
            return False
        h = _hash(token)
        return any(secrets.compare_digest(d["hash"], h) for d in self.devices)

    def list(self) -> list[dict]:
        return [{k: d[k] for k in ("id", "name", "created")} for d in self.devices]

    def remove(self, device_id: str) -> None:
        with self._lock:
            self.devices = [d for d in self.devices if d["id"] != device_id]
            self._save()


def serve_discovery(info: HostInfo, stop: threading.Event, port: int = DISCOVERY_PORT) -> None:
    """LAN에서 앱이 보내는 브로드캐스트에 PC 정보로 응답한다."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", port))
        sock.settimeout(1.0)
    except OSError as e:
        log.warning("자동 검색 비활성화 (UDP %d 사용 불가): %s", port, e)
        return
    with sock:
        while not stop.is_set():
            try:
                data, addr = sock.recvfrom(256)
            except socket.timeout:
                continue
            except OSError:
                break
            if data.strip() == DISCOVERY_MAGIC:
                try:
                    sock.sendto(json.dumps(info.as_dict()).encode(), addr)
                except OSError:
                    pass
