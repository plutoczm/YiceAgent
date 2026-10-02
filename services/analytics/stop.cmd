@echo off
setlocal
set "PROJECT_PYTHON=%CONDA_PREFIX%\python.exe"
if not exist "%PROJECT_PYTHON%" set "PROJECT_PYTHON=D:\Anaconda3\python.exe"
if not exist "%PROJECT_PYTHON%" set "PROJECT_PYTHON=%USERPROFILE%\anaconda3\python.exe"
if not exist "%PROJECT_PYTHON%" set "PROJECT_PYTHON=%USERPROFILE%\miniconda3\python.exe"
if not exist "%PROJECT_PYTHON%" exit /b 1
"%PROJECT_PYTHON%" "%~dp0manage.py" stop
exit /b %errorlevel%
