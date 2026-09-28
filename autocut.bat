@echo off
setlocal

set "ROOT=%~dp0"
set "PY="

rem Priority: project .venv > WorkBuddy hosted venv (has playwright) > PATH python
if exist "%ROOT%.venv\Scripts\python.exe" set "PY=%ROOT%.venv\Scripts\python.exe"
if not defined PY if exist "%ROOT%..\.venv\Scripts\python.exe" set "PY=%ROOT%..\.venv\Scripts\python.exe"
if not defined PY if exist "%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe" set "PY=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if defined PY goto have_py

where python >nul 2>nul && set "PY=python"
if defined PY goto have_py

echo [AutoCut] Python not found. Please install Python 3.10+ and add it to PATH.
pause
exit /b 3

:have_py
if not "%~1"=="" goto with_args

rem Double-click mode (no args): start the web UI, browser opens automatically
"%PY%" "%ROOT%run.py" serve
goto finish

:with_args
"%PY%" "%ROOT%run.py" %*

:finish
echo.
echo [AutoCut] Exited. If there is an error above, screenshot it and send it to the assistant.
pause
exit /b 0
