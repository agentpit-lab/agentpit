@echo off
rem Runs stint.py for cmd and PowerShell; Git Bash uses bin/stint.
python "%~dp0..\scripts\stint.py" %*
