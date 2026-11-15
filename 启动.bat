@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Yuketang Auto Player

python -V >nul 2>nul
if errorlevel 1 goto try_py
python main.py %*
goto done

:try_py
py -3 -V >nul 2>nul
if errorlevel 1 goto no_python
py -3 main.py %*
goto done

:no_python
echo.
echo Python not found. Install Python and tick "Add python.exe to PATH".
echo.
pause
exit /b 1

:done
echo.
echo Done.
pause
