@echo off
cd /d "%~dp0"
echo.
echo ============================================
echo    Boss 直聘 HR 智能招聘助手
echo ============================================
echo.
set "HR_PY=%~dp0.venv\Scripts\python.exe"
if not exist "%HR_PY%" set "HR_PY=C:\Users\DELL\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%HR_PY%" (
    echo 正在启动...
    "%HR_PY%" app.py
) else (
    python app.py
)
echo.
echo 程序已退出。
pause