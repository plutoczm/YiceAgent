@echo off
cd /d "%~dp0"
if not exist ".runtime\v2\envs\main\Scripts\python.exe" (
  echo Project main environment is not installed. See docs\DEVELOPMENT.md.
  exit /b 1
)
".runtime\v2\envs\main\Scripts\python.exe" -B scripts\manage.py down
exit /b %errorlevel%
