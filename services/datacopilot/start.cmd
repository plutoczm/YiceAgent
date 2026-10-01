@echo off
setlocal
set "PROJECT_PYTHON=%CONDA_PREFIX%\python.exe"
if exist "%PROJECT_PYTHON%" goto run
set "PROJECT_PYTHON=D:\Anaconda3\python.exe"
if exist "%PROJECT_PYTHON%" goto run
set "PROJECT_PYTHON=%USERPROFILE%\anaconda3\python.exe"
if exist "%PROJECT_PYTHON%" goto run
set "PROJECT_PYTHON=%USERPROFILE%\miniconda3\python.exe"
if exist "%PROJECT_PYTHON%" goto run
echo Python/Anaconda not found.
exit /b 1
:run
"%PROJECT_PYTHON%" "%~dp0manage.py" start %*
exit /b %errorlevel%
