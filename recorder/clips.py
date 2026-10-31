"""ffmpeg로 마커 구간을 잘라 저장한다."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .config import ClipConfig
from .sessions import Session

_UNSAFE = re.compile(r'[\\/:*?"<>|\s]+')


def safe_name(text: str, limit: int = 40) -> str:
    return _UNSAFE.sub("_", text).strip("_")[:limit] or "clip"


def fmt_ts(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600:d}h{sec % 3600 // 60:02d}m{sec % 60:02d}s"


def ffmpeg_cmd(cfg: ClipConfig, src: str, start: float, end: float, out: str) -> list[str]:
    cmd = [cfg.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
           "-ss", f"{start:.3f}", "-i", src, "-t", f"{end - start:.3f}", "-map", "0"]
    if cfg.reencode:
        cmd += ["-c:v", "libx264", "-crf", "16", "-preset", "medium",
                "-c:a", "aac", "-b:a", "320k"]
    else:
        cmd += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
    return cmd + [out]


def probe_duration(cfg: ClipConfig, src: str) -> float | None:
    probe = str(Path(cfg.ffmpeg).with_name("ffprobe" + Path(cfg.ffmpeg).suffix)) \
        if Path(cfg.ffmpeg).parent != Path(".") else "ffprobe"
    try:
        out = subprocess.run(
            [probe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", src],
            capture_output=True, text=True, timeout=30,
        )
        return float(out.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def export_session(session: Session, cfg: ClipConfig) -> tuple[int, int]:
    """세션의 모든 마커를 잘라 저장. (성공 수, 실패 수)를 돌려주고 session을 갱신한다."""
    if not session.output_path or not Path(session.output_path).exists():
        raise FileNotFoundError(f"녹화 파일을 찾을 수 없습니다: {session.output_path}")
    src = Path(session.output_path)
    out_dir = Path(cfg.output_dir) if cfg.output_dir else src.parent / "clips"
    out_dir.mkdir(parents=True, exist_ok=True)
    total = session.duration or probe_duration(cfg, str(src))

    ok = fail = 0
    for i, m in enumerate(session.markers, 1):
        start, end = m.window(cfg.pre_seconds, cfg.post_seconds, total)
        if end - start < 0.5:
            m.clip_path, m.clip_error = None, "구간이 너무 짧습니다"
            fail += 1
            continue
        name = f"{src.stem}_{i:02d}_{fmt_ts(start)}_{safe_name(m.label)}{src.suffix}"
        out = out_dir / name
        try:
            subprocess.run(ffmpeg_cmd(cfg, str(src), start, end, str(out)),
                           check=True, capture_output=True, text=True, timeout=1800)
            m.clip_path, m.clip_error = str(out), None
            ok += 1
        except FileNotFoundError:
            raise RuntimeError("ffmpeg를 찾을 수 없습니다. 설치 후 config.toml의 clips.ffmpeg를 확인하세요.")
        except subprocess.CalledProcessError as e:
            m.clip_path, m.clip_error = None, (e.stderr or "ffmpeg 오류").strip()[-300:]
            fail += 1
    return ok, fail
