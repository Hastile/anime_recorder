@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
  echo 처음 실행: 가상환경을 만들고 필요한 패키지를 설치합니다...
  py -3 -m venv .venv || python -m venv .venv
  .venv\Scripts\python -m pip install -q -r requirements.txt
)
if not exist config.toml copy config.example.toml config.toml >nul
.venv\Scripts\python -m recorder %*
pause
