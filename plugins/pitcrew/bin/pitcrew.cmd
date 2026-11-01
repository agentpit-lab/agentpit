@echo off
rem Runs pitcrew.py for cmd and PowerShell; Git Bash uses bin/pitcrew.
python "%~dp0..\scripts\pitcrew.py" %*
