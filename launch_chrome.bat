@echo off
setlocal
REM Launch a dedicated Chrome instance with CDP debugging port 9333
set "PORT=9333"
set "PROFILE=%~dp0data\chrome-profile"
if not exist "%PROFILE%" mkdir "%PROFILE%"

set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%LocalAppData%\Google\Chrome\Application\chrome.exe"

if not exist "%CHROME%" (
  echo [ERROR] chrome.exe not found. Edit this script with the correct path.
  pause
  exit /b 1
)

echo Launching Chrome on debug port %PORT%
echo Profile dir: %PROFILE%
echo Log in to Fanqie in the window that opens, then run: python src\probe.py

start "" "%CHROME%" --remote-debugging-port=%PORT% --user-data-dir="%PROFILE%" https://fanqienovel.com
