@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM 进入 bat 所在目录，避免相对路径问题
cd /d "%~dp0"

REM 强制 Python 输出实时刷新，便于看日志
set PYTHONUNBUFFERED=1

REM 你可以改成你自己的 python.exe 绝对路径
set "PYTHON_CMD=python"

echo ==================================================
echo [%date% %time%] Forex AI backtest runner started
echo ==================================================

echo.
echo [%date% %time%] Launching backtest.py ...
"%PYTHON_CMD%" backtest.py
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if "%EXIT_CODE%"=="0" (
    echo [%date% %time%] backtest.py exited normally with code 0.
) else (
    echo [%date% %time%] backtest.py exited with code %EXIT_CODE%.
)

echo.
pause
endlocal
