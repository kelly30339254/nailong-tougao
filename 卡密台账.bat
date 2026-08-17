@echo off
rem 卡密发放台账（双击运行，图形界面）
cd /d "%~dp0"

if exist .venv\Scripts\pythonw.exe (
    start "" .venv\Scripts\pythonw.exe scripts\cardkey_ledger_gui.py
    exit /b 0
)
if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe scripts\cardkey_ledger_gui.py
    exit /b 0
)

echo [错误] 未找到 .venv\Scripts\python.exe
pause
exit /b 1
