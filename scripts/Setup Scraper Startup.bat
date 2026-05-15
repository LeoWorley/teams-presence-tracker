@echo off
:: Double-click this file to register the Teams Presence Scraper as a startup task.
:: A UAC prompt will appear asking for Administrator permission.
::
:: To track multiple users, edit this file and add their names after -Users, e.g.:
::   powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-scraper-task.ps1" -Users "Diego Zepeda","Another Colleague"
::
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-scraper-task.ps1"
pause
