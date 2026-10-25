@echo off
chcp 65001 >nul
setlocal
set "SCRIPT=%~dp0shua.py"
set "PROBE=%TEMP%\shua_pyprobe.txt"
if exist "%SCRIPT%" goto have_script
echo.
echo [ERROR] shua.py not found next to this .bat
echo    expected: %SCRIPT%
echo    Put shua.py and this .bat in the SAME FOLDER, then double-click again.
echo.
pause
exit /b 1

:have_script
cd /d "%~dp0" >nul 2>nul
echo folder: %~dp0
set "PY="
for %%P in (py python python3) do (
    if not defined PY (
        "%%P" -c "print(20261025)" >"%PROBE%" 2>nul
        findstr /C:"20261025" "%PROBE%" >nul 2>nul && set "PY=%%P"
    )
)
if not defined PY goto no_python
del "%PROBE%" >nul 2>nul
"%PY%" -c "import sys; print('python: '+sys.executable)"

"%PY%" "%SCRIPT%" %*
set "RC=%ERRORLEVEL%"
if "%RC%"=="0" goto done
echo.
echo [note] shua.py stopped with exit code %RC%. If the text scrolled away, open cmd in
echo        this folder and run:  py shua.py
:done
echo.
pause
exit /b 0

:no_python
echo.
echo [ERROR] Python is missing or not usable on this computer.
echo         This .bat needs Python 3.8 or newer.
echo.
echo         Option 1 - install Python:  https://www.python.org/downloads/
echo                  tick "Add python.exe to PATH" while installing, then double-click again.
echo.
echo         Option 2 - no Python needed: double-click the .html file in this folder.
for %%F in ("%~dp0*.html") do (
    echo                  it runs in Chrome and does the same job: %%F
    start "" "%%F"
)
echo.
pause
exit /b 1
