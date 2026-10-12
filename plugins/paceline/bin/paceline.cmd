@echo off
rem Runs paceline.py for cmd and PowerShell; Git Bash uses bin/paceline.
python "%~dp0..\scripts\paceline.py" %*
