@echo off
rem 奶龙投稿助手 — PyInstaller 打包脚本（双击运行）
cd /d "%~dp0"

set PYI=.venv\Scripts\pyinstaller.exe
if not exist %PYI% (
    echo [错误] 未找到 %PYI%，请先创建 .venv 并安装 pyinstaller
    pause
    exit /b 1
)

%PYI% --noconfirm --clean --noconsole --onefile ^
    --name "奶龙投稿助手" ^
    --add-data "app/style.qss;app" ^
    --add-data "app/assets;app/assets" ^
    --add-data "app/data;app/data" ^
    main.py

if errorlevel 1 (
    echo [错误] 打包失败，请检查上方输出
    pause
    exit /b 1
)

echo.
echo [完成] 产物：dist\奶龙投稿助手.exe
pause
