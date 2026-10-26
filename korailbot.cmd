@echo off
REM Wrapper: run the korailbot CLI with the project's virtualenv when present.
setlocal
set DIR=%~dp0
if exist "%DIR%.venv\Scripts\python.exe" (
  "%DIR%.venv\Scripts\python.exe" "%DIR%src\cli.py" %*
) else (
  py -3.12 "%DIR%src\cli.py" %*
)
endlocal
