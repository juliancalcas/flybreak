@echo off
REM Double-click this file to launch FlyBreak: fetches the connectome on
REM first run, starts the engine and frontend, opens your browser.
REM Equivalent to running `python -m flybreak` from the repo root.
cd /d "%~dp0.."
python -m flybreak
pause
