@echo off
rem Spusti aplikaciu "Obnova dat PRO" (okno). Dvojklik na tento subor.
cd /d "%~dp0"
start "" pythonw "Obnova dat PRO.pyw"
if errorlevel 1 start "" py -w "Obnova dat PRO.pyw"
