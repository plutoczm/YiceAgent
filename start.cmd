@echo off
cd /d "%~dp0"
if defined WORKFLOWOPS_CONDA_EXE (
  "%WORKFLOWOPS_CONDA_EXE%" run --no-capture-output -n agentopshub python -B scripts\dev.py start
) else (
  "D:\Anaconda3\Scripts\conda.exe" run --no-capture-output -n agentopshub python -B scripts\dev.py start
)
exit /b %errorlevel%
