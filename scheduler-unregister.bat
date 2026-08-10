@echo off
REM context-DB 작업 스케줄러 등록 해제
schtasks /Delete /TN "context-db-ingest" /F
echo [해제 완료] 'context-db-ingest' 작업이 제거되었습니다.
