@echo off
rem jt-live-whisper Windows portable entry: run launcher.py with the bundled
rem Python. Keep this file ASCII-only; launcher.py prints the Chinese text.
setlocal
set "PYTHONHOME="
set "PYTHONPATH="
"%~dp0python\python.exe" "%~dp0launcher.py" %*
set "RC=%ERRORLEVEL%"
if "%RC%"=="0" exit /b 0
rem Double-click runs cmd /c "<this file>": pause so the error stays readable.
rem Delayed expansion keeps special characters in the path inert.
setlocal EnableDelayedExpansion
set "CL=!cmdcmdline!"
if /i "!CL:%~nx0=!"=="!CL!" exit /b %RC%
pause
exit /b %RC%
