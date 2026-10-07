@echo off
rem Launch "RecoveryTool" (GUI). Double-click this file.
cd /d "%~dp0"
start "" pythonw "RecoveryTool.pyw"
if errorlevel 1 start "" py -w "RecoveryTool.pyw"
