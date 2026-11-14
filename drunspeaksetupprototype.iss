; Друнспик — установщик клиента (Inno Setup)
[Setup]
AppName=Drunspeak Client
AppVersion=3.7
DefaultDirName={autopf}\Drunspeak
DefaultGroupName=Drunspeak
OutputDir=.
OutputBaseFilename=Drunspeak_Setup
SetupIconFile="icon.ico"
Compression=lzma2/ultra64
SolidCompression=yes
PrivilegesRequired=admin

[Files]
Source: "client.pyw"; DestDir: "{app}"; Flags: ignoreversion
Source: "server.pyw"; DestDir: "{app}"; Flags: ignoreversion
Source: "icon.ico"; DestDir: "{app}"; Flags: ignoreversion
; Конфиг кладём рядом с приложением, но НЕ перезаписываем существующий:
; ярлык и клиент всегда читают настройки из этой папки.
Source: "config.json"; DestDir: "{app}"; Flags: onlyifdoesntexist skipifsourcedoesntexist
Source: "custom_themes.json"; DestDir: "{app}"; Flags: onlyifdoesntexist skipifsourcedoesntexist
Source: "icons\icon_mic.png"; DestDir: "{app}\icons"; Flags: ignoreversion
Source: "icons\icon_headphones.png"; DestDir: "{app}\icons"; Flags: ignoreversion
Source: "icons\icon_send.png"; DestDir: "{app}\icons"; Flags: ignoreversion
Source: "icons\icon_image.png"; DestDir: "{app}\icons"; Flags: ignoreversion

[Icons]
; Второй аргумент — каталог приложения. Клиент берёт из него config.json,
; темы и иконки, независимо от того, откуда запущен pythonw.exe.
Name: "{autodesktop}\Drunspeak"; Filename: "pythonw.exe"; Parameters: """{app}\client.pyw"" ""{app}"""; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"
Name: "{group}\Drunspeak"; Filename: "pythonw.exe"; Parameters: """{app}\client.pyw"" ""{app}"""; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"

[Run]
; Зависимости клиента: трансляция экрана (mss + pillow), звук (numpy + sounddevice).
Filename: "cmd.exe"; Parameters: "/c python -m pip install numpy sounddevice mss pillow"; StatusMsg: "Установка зависимостей Python..."; Flags: runhidden
