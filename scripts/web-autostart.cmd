@echo off
REM MAKER web dashboard autostart (Task Scheduler, at boot).
REM Started by hand it dies on reboot, and anything proxying to this port
REM then answers 502 until someone notices. Register it instead.
REM Bind 127.0.0.1 so only a local reverse proxy/tunnel reaches it,
REM never the LAN directly. The dashboard has no auth of its own:
REM whoever can reach it can branch, commit and open merge requests.
REM Put an identity policy in front of it before exposing it anywhere.
REM
REM ASCII ONLY on purpose. This machine's ANSI codepage is 949 (CP949/DBCS).
REM cmd.exe parses .cmd files in the ANSI codepage, so UTF-8 Korean bytes get
REM misread and a DBCS lead byte can swallow the newline, splitting commands
REM mid-line. Keep this file 7-bit ASCII regardless of repo comment style.
setlocal
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
if not exist "worklogs\_web" mkdir "worklogs\_web"

REM Do not start a second instance - a port clash would kill the healthy one.
REM Skips are logged to a separate file on purpose: the running server holds
REM web.log open through its own redirect, so appending there would fail noisily.
netstat -ano | findstr /r /c:"127.0.0.1:8790 .*LISTENING" >nul 2>&1
if not errorlevel 1 (
  echo %DATE% %TIME% already listening on 8790 - skip >> "worklogs\_web\skip.log" 2>nul
  exit /b 0
)

echo. >> "worklogs\_web\web.log"
echo ===== %DATE% %TIME% start ===== >> "worklogs\_web\web.log"
python -m xgen_maker web --config maker.config.json --host 127.0.0.1 --port 8790 >> "worklogs\_web\web.log" 2>&1
echo exit=%ERRORLEVEL% >> "worklogs\_web\web.log"
exit /b %ERRORLEVEL%
