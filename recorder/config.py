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
    # 앱 PC 목록에 보일 이름 (비우면 컴퓨터 이름)
    name: str = ""
    # 같은 와이파이에서 앱이 PC를 자동으로 찾게 함 (UDP 8766)
    discovery: bool = True


@dataclass
class ObsConfig:
    host: str = "127.0.0.1"
    port: int = 4455
    password: str = ""
    timeout: float = 3.0
    # 녹화 전용 브라우저 창을 자동으로 연결할 OBS 소스 이름 (비우면 건드리지 않음)
    video_source: str = ""  # 윈도우 캡처
    audio_source: str = ""  # 응용 프로그램 오디오 캡처


@dataclass
class BrowserConfig:
    # 비우면 크롬 → 엣지 순서로 자동으로 찾는다.
    # 평소 크롬을 쓰면 녹화는 엣지로 하는 것이 가장 확실하다 (OBS가 창을 헷갈리지 않음).
    executable: str = ""
    window_size: list[int] = field(default_factory=lambda: [1920, 1080])
    # 예: 보조 모니터에 띄우려면 [1920, 0]
    window_position: list[int] = field(default_factory=list)
    # 영상을 창에 꽉 채우기 (사이트 헤더/댓글 없이 영상만 녹화)
    fill_video: bool = True
    # 페이지에서 영상을 찾을 때까지 기다리는 최대 시간(초)
    video_timeout: float = 20.0
    # 창을 띄운 뒤 맨 뒤로 보내고 하던 창으로 포커스를 돌려줌
    send_to_back: bool = True
    # 녹화가 끝나면 녹화용 창 닫기 (다음 화 자동재생 방지)
    close_on_stop: bool = True
    extra_args: list[str] = field(default_factory=list)
    # 창이 뜨기를 기다리는 시간(초)
    wait_seconds: float = 5.0


@dataclass
class RecordingConfig:
    # 녹화가 끝나면 파일 이름을 제목으로 바꿈 (예: "진격의 거인 3화.mkv")
    rename_to_title: bool = True


@dataclass
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    obs: ObsConfig = field(default_factory=ObsConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    recording: RecordingConfig = field(default_factory=RecordingConfig)
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
        _fill(cfg.recording, raw.get("recording", {}))
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
