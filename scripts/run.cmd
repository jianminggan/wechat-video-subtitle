@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set PYTHONNOUSERSITE=
set PYTHONSAFEPATH=
set PYTHONPATH=

where python3.14 >nul 2>&1
if %errorlevel% equ 0 goto python314

where python >nul 2>&1
if %errorlevel% equ 0 goto python

where py >nul 2>&1
if %errorlevel% equ 0 goto py

echo Python 3.10 or newer was not found in PATH. 1>&2
exit /b 2

:python314
python3.14 -u "%~dp0main.py" %*
exit /b %errorlevel%

:python
python -u "%~dp0main.py" %*
exit /b %errorlevel%

:py
py -3 -u "%~dp0main.py" %*
exit /b %errorlevel%
