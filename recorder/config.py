"""설정 로딩. config.toml이 없으면 기본값으로 동작한다."""

from __future__ import annotations

import secrets
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8765
    token: str = ""


@dataclass
class ObsConfig:
    host: str = "127.0.0.1"
    port: int = 4455
    password: str = ""
    timeout: float = 3.0


@dataclass
class BrowserConfig:
    # 비어 있으면 기본 브라우저로 연다. "{url}" 자리에 주소가 들어간다.
    command: list[str] = field(default_factory=list)
    # 페이지를 연 뒤 녹화를 시작하기까지 기다리는 시간(초)
    wait_seconds: float = 5.0


@dataclass
class ClipConfig:
    ffmpeg: str = "ffmpeg"
    # "지금 표시" 마커 기준 앞/뒤로 잘라낼 길이(초)
    pre_seconds: float = 10.0
    post_seconds: float = 5.0
    # False면 재인코딩 없이 자름(빠르고 무손실, 키프레임 단위로 잘림)
    reencode: bool = False
    # 녹화가 끝나면 마커 구간을 자동으로 잘라 저장
    auto_export: bool = True
    # 비어 있으면 <녹화 파일 폴더>/clips
    output_dir: str = ""
    quick_labels: list[str] = field(
        default_factory=lambda: ["명장면", "웃김", "액션", "대사", "OP/ED"]
    )


@dataclass
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    obs: ObsConfig = field(default_factory=ObsConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    clips: ClipConfig = field(default_factory=ClipConfig)
    data_dir: Path = Path("data")


def _fill(dc, values: dict):
    for key, value in values.items():
        if hasattr(dc, key):
            setattr(dc, key, value)
    return dc


def load_config(path: str | Path | None = "config.toml") -> Config:
    cfg = Config()
    if path and Path(path).exists():
        with open(path, "rb") as f:
            raw = tomllib.load(f)
        _fill(cfg.server, raw.get("server", {}))
        _fill(cfg.obs, raw.get("obs", {}))
        _fill(cfg.browser, raw.get("browser", {}))
        _fill(cfg.clips, raw.get("clips", {}))
        if "data_dir" in raw.get("storage", {}):
            cfg.data_dir = Path(raw["storage"]["data_dir"])

    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    if not cfg.server.token or cfg.server.token == "change-me":
        cfg.server.token = _load_or_create_token(cfg.data_dir / "token.txt")
    return cfg


def _load_or_create_token(path: Path) -> str:
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(18)
    path.write_text(token, encoding="utf-8")
    return token
