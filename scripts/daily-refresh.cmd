@echo off
REM 매일 08:00 작업 스케줄러가 실행 — 레포 안전 최신화 + 지식그래프 증분 반영.
REM 작업 브랜치는 절대 건드리지 않는다(xgen_maker/kg/refresh.py 불변식).
setlocal
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
if not exist "worklogs\_refresh" mkdir "worklogs\_refresh"
echo. >> "worklogs\_refresh\refresh.log"
echo ===== %DATE% %TIME% ===== >> "worklogs\_refresh\refresh.log"
python -m xgen_maker kg refresh --config maker.config.json >> "worklogs\_refresh\refresh.log" 2>&1
echo exit=%ERRORLEVEL% >> "worklogs\_refresh\refresh.log"
exit /b %ERRORLEVEL%
