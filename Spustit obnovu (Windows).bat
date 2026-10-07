@echo off
rem Spusti aplikaciu Obnova dat (okno). Dvojklik na tento subor.
cd /d "%~dp0"
start "" pythonw "Obnova dat.pyw"
if errorlevel 1 (
  rem fallback ak pythonw nie je v PATH
  start "" py -w "Obnova dat.pyw"
)
