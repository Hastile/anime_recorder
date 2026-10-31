"""실행: python -m recorder [--config config.toml] [--demo]"""

from __future__ import annotations

import argparse
import logging
import socket

import uvicorn

from .config import load_config
from .core import Recorder
from .obs import FakeObs, ObsController
from .server import create_app


def _lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def main() -> None:
    p = argparse.ArgumentParser(prog="recorder", description="Anime Recorder 서버")
    p.add_argument("--config", default="config.toml")
    p.add_argument("--demo", action="store_true", help="OBS 없이 가짜 녹화기로 UI 확인")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config(args.config)
    obs = FakeObs() if args.demo else ObsController(
        cfg.obs.host, cfg.obs.port, cfg.obs.password, cfg.obs.timeout)
    app = create_app(Recorder(cfg, obs))

    print("\n" + "=" * 56)
    print(" Anime Recorder 실행 중" + (" (데모 모드)" if args.demo else ""))
    print(f"  같은 와이파이:  http://{_lan_ip()}:{cfg.server.port}")
    print(f"  접속 토큰:      {cfg.server.token}")
    print("  폰 앱: PC 목록에서 이 PC를 고르면 여기에 PIN이 뜹니다.")
    print("  밖에서 쓰려면 README의 Tailscale 설정을 참고하세요.")
    print("=" * 56 + "\n")
    uvicorn.run(app, host=cfg.server.host, port=cfg.server.port, log_level="warning")


if __name__ == "__main__":
    main()
