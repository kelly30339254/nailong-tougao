@echo off
rem 卡密发放台账（双击运行，菜单操作）
cd /d "%~dp0"
set PY=.venv\Scripts\python.exe

if not exist %PY% (
    echo [错误] 未找到 %PY%
    pause
    exit /b 1
)

:menu
echo.
echo ======== 卡密台账 ========
echo  1. 取卡发给买家
echo  2. 查看库存与发放记录
echo  3. 手动标记指定卡密
echo  4. 导入新批次卡密
echo  0. 退出
echo.
set /p choice=请选择（0-4）：
if "%choice%"=="1" goto give
if "%choice%"=="2" goto status
if "%choice%"=="3" goto mark
if "%choice%"=="4" goto import
if "%choice%"=="0" exit /b 0
goto menu

:give
set /p count=取几张：
set /p note=买家备注（微信/闲鱼号，可留空）：
%PY% scripts\cardkey_ledger.py give %count% --note "%note%"
echo.
pause
goto menu

:status
%PY% scripts\cardkey_ledger.py status
echo.
pause
goto menu

:mark
set /p keys=卡密（多张用空格分隔）：
set /p note=标记原因（可留空）：
%PY% scripts\cardkey_ledger.py mark %keys% --note "%note%"
echo.
pause
goto menu

:import
set /p file=卡密文件路径（如 server\cardkeys-新批次.json）：
%PY% scripts\cardkey_ledger.py import "%file%"
echo.
pause
goto menu
