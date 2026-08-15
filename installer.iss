; 奶龙投稿助手 — Windows 安装程序脚本（Inno Setup 6）
; 用法：build_installer.bat 会自动传入 /DAppVersion=x.y.z
; 激活数据在 %APPDATA%\NailongPost\license.json，与安装目录无关，
; 覆盖安装 / 卸载重装均不影响已激活状态。
#ifndef AppVersion
  #define AppVersion "1.1.0"
#endif

[Setup]
AppId={{B3F71A2E-8C4D-4E6A-9D5B-2F7A1C3E5D9B}
AppName=奶龙投稿助手
AppVersion={#AppVersion}
AppVerName=奶龙投稿助手 {#AppVersion}
DefaultDirName={autopf}\奶龙投稿助手
DefaultGroupName=奶龙投稿助手
OutputDir=dist
OutputBaseFilename=奶龙投稿助手-{#AppVersion}-windows-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："

[Files]
Source: "dist\奶龙投稿助手.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\奶龙投稿助手"; Filename: "{app}\奶龙投稿助手.exe"
Name: "{group}\卸载 奶龙投稿助手"; Filename: "{uninstallexe}"
Name: "{autodesktop}\奶龙投稿助手"; Filename: "{app}\奶龙投稿助手.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\奶龙投稿助手.exe"; Description: "运行 奶龙投稿助手"; Flags: nowait postinstall skipifsilent
