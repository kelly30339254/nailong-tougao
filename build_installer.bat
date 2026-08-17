@echo off
rem 奶龙投稿助手 — Windows 安装版打包（PyInstaller + Inno Setup，双击运行）
rem 产物：dist\奶龙投稿助手-<版本>-windows-setup.exe
cd /d "%~dp0"

rem 注意：pyinstaller.exe 包装器在中文路径下会静默失败，统一用 python -m PyInstaller
set PY=.venv\Scripts\python.exe
if not exist %PY% (
    echo [错误] 未找到 %PY%，请先创建 .venv 并安装 pyinstaller
    pause
    exit /b 1
)

set ISCC=
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if exist "C:\Program Files\Inno Setup 6\ISCC.exe" set "ISCC=C:\Program Files\Inno Setup 6\ISCC.exe"
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC (
    echo [错误] 未找到 Inno Setup 6，请先安装：winget install JRSoftware.InnoSetup
    pause
    exit /b 1
)

for /f %%v in ('%PY% -c "from app import APP_VERSION; print(APP_VERSION)"') do set VER=%%v
echo [信息] 版本号：%VER%

rem 第一步：PyInstaller 打单文件 exe（走 .spec，已关闭 UPX）
%PY% -m PyInstaller --noconfirm --clean "奶龙投稿助手.spec"
if errorlevel 1 (
    echo [错误] PyInstaller 打包失败，请检查上方输出
    pause
    exit /b 1
)

rem 第二步：Inno Setup 生成安装程序
"%ISCC%" /DAppVersion=%VER% installer.iss
if errorlevel 1 (
    echo [错误] Inno Setup 打包失败，请检查上方输出
    pause
    exit /b 1
)

echo.
echo [完成] 产物：dist\奶龙投稿助手-%VER%-windows-setup.exe
pause
