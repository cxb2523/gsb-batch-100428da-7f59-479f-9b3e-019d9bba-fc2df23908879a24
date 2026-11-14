@echo off
chcp 65001 >nul
title 企业微信智能客服

:: ── Check .env ──────────────────────────────────────────
if not exist ".env" (
    echo [错误] 未找到 .env 文件！
    echo.
    echo 请复制 .env.example 为 .env，然后填入你的 DEEPSEEK_API_KEY：
    echo   copy .env.example .env
    echo   notepad .env
    echo.
    pause
    exit /b 1
)

:: ── Create runtime directories ──────────────────────────
if not exist "data" mkdir data
if not exist "data\chat_raw" mkdir data\chat_raw
if not exist "data\context" mkdir data\context
if not exist "data\pending" mkdir data\pending
if not exist "data\qdrant" mkdir data\qdrant
if not exist "data\state" mkdir data\state
if not exist "logs" mkdir logs

:: ── Tcl/Tk paths for tkinter (PyInstaller hook workaround) ──
set TCL_LIBRARY=%~dp0_internal\_tcl_data
set TK_LIBRARY=%~dp0_internal\_tk_data

:: ── PaddlePaddle DLLs (mklml, mkldnn, etc.) ──
set PATH=%~dp0_internal;%~dp0;%PATH%

:: ── HuggingFace mirror for BGE model download (China) ──
set HF_ENDPOINT=https://hf-mirror.com
set HF_HUB_ENABLE_HF_TRANSFER=1
set KMP_DUPLICATE_LIB_OK=TRUE

:: ── Launch ──────────────────────────────────────────────
echo 正在启动企业微信智能客服...
echo.
"启动.exe"
if %ERRORLEVEL% neq 0 (
    echo.
    echo [错误] 程序异常退出，错误码: %ERRORLEVEL%
    echo 请检查 logs\monitor.log 了解详情。
    pause
)
