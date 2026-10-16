@echo off
rem Picks a Python 3.12 to 3.14 and hands over to the bootstrap, which builds the environment on first use.
setlocal
set "PY="
call :probe py -3.14
if defined PY goto run
call :probe py -3.13
if defined PY goto run
call :probe py -3.12
if defined PY goto run
call :probe python
if defined PY goto run
>&2 echo dbwise: needs Python 3.12 to 3.14; tried py -3.14, py -3.13, py -3.12, python. Install one and try again.
exit /b 1
:run
%PY% "%~dp0..\scripts\bootstrap.py" --run-cli %*
exit /b %errorlevel%
:probe
if defined PY exit /b 0
call %* -c "import sys; sys.exit(0 if (3, 12) <= sys.version_info[:2] < (3, 15) else 1)" <nul >nul 2>&1
if not errorlevel 1 set "PY=%*"
exit /b 0
