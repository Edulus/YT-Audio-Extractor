; ============================================================================
;  YT Audio Extractor - Inno Setup installer
;
;  A small per-user installer (no admin rights): copies the app, adds Start
;  menu and desktop shortcuts, and explains what will happen on the PC. It
;  bundles no binaries. On first launch YT-Audio-Extractor.bat downloads a
;  private Python, ffmpeg and Deno into the app's bin\ folder, so the
;  installer stays around 1 MB.
;
;  Build with the Inno Setup Compiler:   iscc installer.iss
;  Output: dist\YT-Audio-Extractor-Setup-<version>.exe
;  The GitHub workflow .github/workflows/installer.yml builds it for each
;  release and attaches it there.
; ============================================================================

#define AppName "YT Audio Extractor"
#define AppVersion "4.5"
#define AppPublisher "Edward Kasimir"
#define AppURL "https://github.com/Edulus/YT-Audio-Extractor"
#define AppExeName "YT-Audio-Extractor.bat"
#define AppIcon "YT-Audio-Extractor.ico"

[Setup]
AppId={{8F2A6C71-9D4E-4B3A-AE12-YTAUDIO000001}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
; Per-user install: {autopf} is %LOCALAPPDATA%\Programs here. The app must be
; able to write its bin\ folder on first launch, which Program Files would not
; allow without admin rights.
PrivilegesRequired=lowest
DefaultDirName={autopf}\YT-Audio-Extractor
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
InfoBeforeFile=installer-info.txt
LicenseFile=LICENSE
OutputDir=dist
OutputBaseFilename=YT-Audio-Extractor-Setup-{#AppVersion}
SetupIconFile=YT-Audio-Extractor.ico
UninstallDisplayIcon={app}\{#AppIcon}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
FinishedLabel=YT Audio Extractor is installed.%n%nThe first time it opens, a window shows it downloading its helper programs (about 160 MB, once). Keep that window open while you use the app.

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "app.py";                    DestDir: "{app}"; Flags: ignoreversion
Source: "requirements.txt";          DestDir: "{app}"; Flags: ignoreversion
Source: "YT-Audio-Extractor.bat";    DestDir: "{app}"; Flags: ignoreversion
Source: "fetch-binaries.ps1";        DestDir: "{app}"; Flags: ignoreversion
Source: "YT-Audio-Extractor.ico";    DestDir: "{app}"; Flags: ignoreversion
Source: "README.md";                 DestDir: "{app}"; Flags: ignoreversion
Source: "CHANGELOG.md";              DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE";                   DestDir: "{app}"; Flags: ignoreversion
Source: "templates\*";               DestDir: "{app}\templates"; Flags: ignoreversion recursesubdirs
Source: "static\*";                  DestDir: "{app}\static";    Flags: ignoreversion recursesubdirs

[Icons]
Name: "{group}\{#AppName}";            Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#AppIcon}"
Name: "{group}\Uninstall {#AppName}";  Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";      Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#AppIcon}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Open {#AppName} now"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent shellexec

[UninstallRun]
; Stop the app if it is running, so its files can be removed. Only this app's
; own private Python is stopped, never any other program.
Filename: "powershell.exe"; Parameters: "-NoProfile -Command ""Get-Process python -ErrorAction SilentlyContinue | Where-Object {{ $_.Path -like '{app}\bin\python\*' } | Stop-Process -Force"""; Flags: runhidden; RunOnceId: "StopApp"

[UninstallDelete]
; Everything the launcher downloaded or generated after install.
Type: filesandordirs; Name: "{app}\bin"
Type: filesandordirs; Name: "{app}\__pycache__"
