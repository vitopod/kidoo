@echo off
where py >nul 2>&1
if not errorlevel 1 goto python_launcher
where python >nul 2>&1
if not errorlevel 1 goto python_command
echo Kidoo needs Python 3.9 or newer. Install Python and try again. 1>&2
exit /b 1
:python_launcher
py -3 "%~dp0server\setup_wizard.py" %*
exit /b %errorlevel%
:python_command
python "%~dp0server\setup_wizard.py" %*
exit /b %errorlevel%
