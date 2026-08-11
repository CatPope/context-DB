@echo off
chcp 65001 >nul
REM context-DB CLI wrapper. Calls cli.py in this folder.
REM Add this folder to PATH to use `context-db <command>` anywhere.
python "%~dp0src\cli.py" %*
