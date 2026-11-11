@echo off
rem Double-click to start ApplyPilot. It opens the dashboard in your browser.
rem Close this window (or press Ctrl+C) to stop it.
title ApplyPilot
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
  echo ApplyPilot needs uv. Install it from https://docs.astral.sh/uv/ and try again.
  pause
  exit /b 1
)
uv run applypilot start
if errorlevel 1 pause
