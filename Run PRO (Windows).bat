@echo off
rem Launch "RecoveryTool PRO" (GUI). Double-click this file.
cd /d "%~dp0"
start "" pythonw "RecoveryTool PRO.pyw"
if errorlevel 1 start "" py -w "RecoveryTool PRO.pyw"
