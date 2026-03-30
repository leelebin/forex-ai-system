@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM 进入 bat 所在目录，避免相对路径问题
cd /d "%~dp0"

REM 强制 Python 输出实时刷新，便于看日志
set PYTHONUNBUFFERED=1

REM 你可以改成你自己的 python.exe 绝对路径
set "PYTHON_CMD=python"

echo ==================================================
echo [%date% %time%] Forex AI resilient runner started
echo ==================================================

:RESTART
echo.
echo [%date% %time%] Launching main.py ...

REM /B 不新开窗口，/HIGH 提高优先级，/WAIT 等待进程结束后再执行下一行
start "" /B /HIGH /WAIT cmd /c "%PYTHON_CMD% main.py"
set "EXIT_CODE=%ERRORLEVEL%"

if "%EXIT_CODE%"=="0" (
    echo [%date% %time%] main.py exited normally with code 0. Restarting in 3 seconds...
) else (
    echo [%date% %time%] main.py crashed/terminated with code %EXIT_CODE%. Restarting in 3 seconds...
)

timeout /t 3 /nobreak >nul
goto RESTART

