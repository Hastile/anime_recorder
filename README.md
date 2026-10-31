# Anime Recorder

밖에서 **폰으로 집 PC의 OBS 녹화를 조작하는 전용 앱**입니다.
녹화는 PC에서 원본 화질로 되고, 폰은 리모컨 역할만 합니다.

- **원터치 녹화**: 주소를 넣고 "녹화 시작"을 누르면 PC에서 페이지가 열리고 OBS가 녹화를 시작합니다. 자동 정지 시간도 정할 수 있어요 (예: 25분).
- **실시간 미리보기**: 지금 녹화되는 화면을 폰에서 2~3초 간격으로 확인합니다. 원격 데스크톱이 필요 없어요.
- **★ 장면 표시**: 보다가 쓸 만한 장면에서 버튼을 누르면 그 시점이 기록됩니다. 빠른 라벨(명장면/웃김/액션 등)과 "구간 시작/끝"도 있습니다.
- **자동 클립**: 녹화가 끝나면 표시한 장면을 ffmpeg로 **재인코딩 없이** 잘라서 `녹화폴더/clips`에 저장합니다. 편집할 때 클립만 끌어다 쓰면 됩니다.
- **예약 녹화**: 매주 요일 반복이나 특정 날짜 1회.
- **기록**: 녹화마다 표시한 장면과 클립 결과를 모아서 보여주고, 라벨 수정, 다시 자르기도 됩니다.

```
[폰: 전용 앱(PWA)] ──Tailscale(암호화)──▶ [집 PC: recorder 서버] ──WebSocket──▶ [OBS]
                                                    └── ffmpeg로 클립 자르기
```

> DRM이 걸린 OTT(넷플릭스 등)는 OBS로 캡처하면 검은 화면이 나옵니다. 이 앱은 DRM을 우회하지 않으며, DRM이 없는 소스용입니다.

---

## 1. PC 설정 (Windows, 한 번만)

1. **Python 3.11 이상**을 설치합니다 (설치할 때 "Add to PATH"에 체크).
2. **ffmpeg**를 설치합니다: PowerShell에서 `winget install Gyan.FFmpeg`
3. **OBS 설정**
   - `도구 → WebSocket 서버 설정`에서 **WebSocket 서버 활성화**를 켭니다. 포트(4455)와 비밀번호를 확인해 두세요.
   - `설정 → 출력 → 녹화`
     - 녹화 형식: **mkv** (녹화가 중간에 끊겨도 파일이 살아남습니다)
     - 인코더: NVENC/AMF/QSV 같은 하드웨어 인코더, 품질 CQP 18~20 정도
     - **키프레임 간격: 1초**. 무손실 자르기는 키프레임 단위로 잘리기 때문에 짧을수록 정확합니다.
   - 장면에 **화면 캡처** 또는 **윈도우 캡처**(브라우저)를 넣어 둡니다.
4. 이 폴더를 PC에 내려받고, `config.example.toml`을 `config.toml`로 복사해서 OBS 비밀번호를 적습니다.
5. **`start.bat`을 더블클릭**합니다. 처음 실행할 때는 필요한 패키지를 알아서 설치합니다.
   창에 나오는 **접속 토큰**을 메모해 두세요 (`data/token.txt`에도 저장됩니다).
6. 절전 모드와 모니터 꺼짐을 해제합니다 (`설정 → 시스템 → 전원`). 모니터가 꺼지면 캡처가 검게 나올 수 있어요.

> 윈도우 시작 시 자동 실행: `Win+R` → `shell:startup` → `start.bat`의 바로가기를 넣어 두세요.

## 2. 밖에서 접속: Tailscale (무료, 포트 열기 불필요)

1. PC와 폰에 [Tailscale](https://tailscale.com/download)을 설치하고 같은 계정으로 로그인합니다.
2. PC의 PowerShell에서 한 번 실행합니다:
   ```
   tailscale serve --bg 8765
   ```
   그러면 `https://<PC이름>.<tailnet>.ts.net` 주소가 생깁니다 (`tailscale serve status`로 확인).
   HTTPS여야 폰에서 **앱으로 설치**하는 기능이 정상 동작합니다.
3. 폰 브라우저에서 그 주소를 열고 토큰을 입력합니다.
4. **홈 화면에 추가**하면 전용 앱처럼 쓸 수 있습니다.
   - 안드로이드 크롬: 메뉴 → "앱 설치" 또는 "홈 화면에 추가"
   - 아이폰 사파리: 공유 → "홈 화면에 추가"

> 같은 와이파이에서는 서버 창에 나온 `http://192.168.x.x:8765`로도 접속됩니다.
> 공유기 포트포워딩으로 인터넷에 직접 여는 것은 권장하지 않습니다.

## 3. 사용 흐름

1. 밖에서 앱 열기 → 주소, 제목 입력 → 자동 정지 "25분" → **● 녹화 시작**
2. 폰으로 같은 에피소드를 보면서 쓸 장면이 나오면 **★ 지금 표시** (기본: 그 시점 10초 전 ~ 5초 후)
   - 긴 장면은 **구간 시작 → 구간 끝**
3. 자동 정지되거나 ■ 정지를 누르면 표시한 장면이 자동으로 잘려 `clips/` 폴더에 저장됩니다.
4. 집에 와서 편집 프로그램에 `clips/` 폴더만 불러오면 끝.

> 폰으로 보는 영상과 PC 녹화는 시작 시점이 약간 다를 수 있습니다. 앞뒤 여유(`pre_seconds`/`post_seconds`)를 넉넉히 두면 편합니다.

OBS에서 직접 녹화를 시작한 경우에도 앱에서 ★를 누르면 자동으로 세션이 만들어집니다.

## 설정 (`config.toml`)

| 항목 | 설명 |
|---|---|
| `browser.command` | 페이지를 열 브라우저 명령. 예: 크롬 전체화면 `["C:/Program Files/Google/Chrome/Application/chrome.exe", "--new-window", "--start-fullscreen", "{url}"]` |
| `browser.wait_seconds` | 페이지를 연 뒤 녹화 시작까지 기다리는 시간 |
| `clips.pre_seconds` / `post_seconds` | ★ 표시 기준 앞뒤로 자를 길이 |
| `clips.reencode` | `false`: 무손실 고속 (키프레임 단위) / `true`: 프레임 단위로 정확하지만 느림 |
| `clips.auto_export` | 녹화가 끝나면 자동으로 클립 만들기 |
| `clips.quick_labels` | ★ 아래 빠른 라벨 버튼 |

## 개발

```bash
pip install -r requirements.txt pytest httpx
python -m recorder --demo     # OBS 없이 UI 확인 (가짜 녹화기)
python -m pytest
```

- `recorder/server.py`: HTTP API (모든 `/api/*`는 `Authorization: Bearer <토큰>` 필요)
- `recorder/core.py`: 녹화, 마커, 자동 정지, 예약 실행
- `recorder/obs.py`: OBS WebSocket v5 제어
- `recorder/clips.py`: ffmpeg 자르기
- `recorder/static/`: 폰 앱 (HTML/JS, PWA)
