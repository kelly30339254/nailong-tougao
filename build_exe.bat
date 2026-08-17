@echo off
rem 奶龙投稿助手 — PyInstaller 打包脚本（双击运行）
cd /d "%~dp0"

rem 注意：pyinstaller.exe 包装器在中文路径下会静默失败，统一用 python -m PyInstaller
set PY=.venv\Scripts\python.exe
if not exist %PY% (
    echo [错误] 未找到 %PY%，请先创建 .venv 并安装 pyinstaller
    pause
    exit /b 1
)

rem 统一走 .spec（已关闭 UPX，避免 Win10 上 QtWidgets DLL 加载失败）
%PY% -m PyInstaller --noconfirm --clean "奶龙投稿助手.spec"

if errorlevel 1 (
    echo [错误] 打包失败，请检查上方输出
    pause
    exit /b 1
)

echo.
echo [完成] 产物：dist\奶龙投稿助手.exe
pause
