@echo off
setlocal
set "APPS_DIR=%~dp0"
for %%I in ("%APPS_DIR%..") do set "ROOT=%%~fI"
set "LOG=%TEMP%\grip-menubar.log"
echo [%DATE% %TIME%] launching Grip from %ROOT%>>"%LOG%"
echo python=%ROOT%\.venv\Scripts\python.exe>>"%LOG%"
set "PATH=%ROOT%\.venv\Scripts;%PATH%"
set "GRIP_MENUBAR_DIRECT=1"
cd /d "%ROOT%"
"%ROOT%\.venv\Scripts\python.exe" -c "from grip.native_app import run_native_menubar; run_native_menubar()"
