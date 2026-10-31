@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
rem ---------------------------------------------------------------
rem  PC에서 폰으로 Anime Recorder(연동판)를 설치합니다.
rem  폰 브라우저로 받으면 "기기 보호를 위해 앱 차단됨"이 뜨는 경우에 사용하세요.
rem  (그 차단은 인터넷에서 받아 설치할 때만 걸리고, PC에서 설치하는 adb에는 걸리지 않습니다)
rem ---------------------------------------------------------------
set "TOOLS=%~dp0tools"
set "ADB=%TOOLS%\platform-tools\adb.exe"
if not exist "%TOOLS%" mkdir "%TOOLS%"

if not exist "%ADB%" (
  echo [1/3] 설치 도구^(adb^) 내려받는 중...
  powershell -NoProfile -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest 'https://dl.google.com/android/repository/platform-tools-latest-windows.zip' -OutFile '%TOOLS%\platform-tools.zip'; Expand-Archive '%TOOLS%\platform-tools.zip' '%TOOLS%' -Force" || goto fail
)

echo [2/3] 최신 앱 내려받는 중...
powershell -NoProfile -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest 'https://github.com/Hastile/anime_recorder/releases/download/android-latest/AnimeRecorder.apk' -OutFile '%TOOLS%\AnimeRecorder.apk'" || goto fail

echo.
echo [3/3] 폰 연결 방법을 고르세요
echo   1. USB 케이블
echo   2. 무선 (PC와 폰이 같은 와이파이)
choice /c 12 /n /m "번호: "
if errorlevel 2 goto wireless

:usb
echo.
echo  폰: 설정 ^> 휴대전화 정보 ^> 소프트웨어 정보 ^> "빌드번호"를 7번 눌러 개발자 옵션을 켭니다.
echo  폰: 설정 ^> 개발자 옵션 ^> "USB 디버깅" 켜기
echo  USB로 연결하고, 폰에 "USB 디버깅을 허용하시겠습니까?"가 뜨면 허용을 누르세요.
echo  기다리는 중...
"%ADB%" wait-for-usb-device || goto fail
goto install

:wireless
echo.
echo  폰: 설정 ^> 휴대전화 정보 ^> 소프트웨어 정보 ^> "빌드번호"를 7번 눌러 개발자 옵션을 켭니다.
echo  폰: 설정 ^> 개발자 옵션 ^> "무선 디버깅" 켜기 ^> 들어가서 "페어링 코드로 기기 페어링"
set /p PAIR_ADDR=  페어링 화면의 IP 주소 및 포트 (예: 192.168.0.5:37123): 
set /p PAIR_CODE=  페어링 코드 6자리: 
"%ADB%" pair %PAIR_ADDR% %PAIR_CODE% || goto fail
echo.
set /p CONN_ADDR=  무선 디버깅 화면 위쪽의 "IP 주소 및 포트" (예: 192.168.0.5:41234): 
"%ADB%" connect %CONN_ADDR% || goto fail
"%ADB%" -s %CONN_ADDR% wait-for-device || goto fail
set "ANDROID_SERIAL=%CONN_ADDR%"

:install
echo.
echo 설치하는 중...
"%ADB%" install -r "%TOOLS%\AnimeRecorder.apk" || goto fail
echo.
echo ==============================================
echo  설치 완료! 폰에서 Anime Recorder를 여세요.
echo  다음 업데이트도 이 파일을 다시 실행하면 됩니다.
echo  (원하면 개발자 옵션의 USB/무선 디버깅은 꺼도 됩니다)
echo ==============================================
pause
exit /b 0

:fail
echo.
echo 설치하지 못했습니다. 위의 메시지를 확인하세요.
echo  - "INSTALL_FAILED_UPDATE_INCOMPATIBLE": 폰의 기존 앱을 지운 뒤 다시 실행하세요.
echo  - 기기가 안 보이면: 케이블/와이파이, 디버깅 허용 여부를 확인하세요.
pause
exit /b 1
