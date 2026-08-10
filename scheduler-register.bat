@echo off
REM context-DB 백그라운드 적재를 Windows 작업 스케줄러에 등록한다.
REM 기본: 10분마다 `context-db ingest` 1회 실행(멱등 → 신규분만 반영).
REM 사용: scheduler-register.bat [주기(분)]   예) scheduler-register.bat 5

set INTERVAL=%1
if "%INTERVAL%"=="" set INTERVAL=10

schtasks /Create /TN "context-db-ingest" ^
  /TR "\"%~dp0context-db.bat\" ingest" ^
  /SC MINUTE /MO %INTERVAL% /F

echo.
echo [등록 완료] 'context-db-ingest' 작업이 %INTERVAL%분 주기로 실행됩니다.
echo   상태 확인 : schtasks /Query /TN context-db-ingest
echo   즉시 실행 : schtasks /Run   /TN context-db-ingest
echo   해제      : scheduler-unregister.bat
