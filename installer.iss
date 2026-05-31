; ============================================================================
;  YT Audio Extractor - Inno Setup installer (OPTIONAL)
;
;  Bundles the app and (if present) the bin/ binaries, and creates Start Menu
;  and desktop shortcuts using YT-Audio-Extractor.ico. It does NOT install
;  Python - it only checks for it and links python.org if absent. The .bat
;  launcher remains fully standalone; this installer is just polish.
;
;  Build with the Inno Setup Compiler:   iscc installer.iss
;  Output: dist\YT-Audio-Extractor-Setup.exe
; ============================================================================

#define AppName "YT Audio Extractor"
#define AppVersion "2.9"
#define AppPublisher "Edward Yersh"
#define AppExeName "YT-Audio-Extractor.bat"
#define AppIcon "YT-Audio-Extractor.ico"

[Setup]
AppId={{8F2A6C71-9D4E-4B3A-AE12-YTAUDIO000001}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\YT-Audio-Extractor
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=YT-Audio-Extractor-Setup
SetupIconFile=YT-Audio-Extractor.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Per-user install by default so no admin rights are needed.
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
; Core app
Source: "app.py";                    DestDir: "{app}"; Flags: ignoreversion
Source: "requirements.txt";          DestDir: "{app}"; Flags: ignoreversion
Source: "YT-Audio-Extractor.bat";    DestDir: "{app}"; Flags: ignoreversion
Source: "fetch-binaries.ps1";        DestDir: "{app}"; Flags: ignoreversion
Source: "YT-Audio-Extractor.ico";    DestDir: "{app}"; Flags: ignoreversion
Source: "README.md";                 DestDir: "{app}"; Flags: ignoreversion
Source: "SETUP.md";                  DestDir: "{app}"; Flags: ignoreversion
Source: "templates\*";               DestDir: "{app}\templates"; Flags: ignoreversion recursesubdirs
Source: "static\*";                  DestDir: "{app}\static";    Flags: ignoreversion recursesubdirs
; Bundled binaries - included ONLY if you have already populated bin/.
; If bin/ is empty/absent, the launcher downloads them on first run instead.
Source: "bin\*"; DestDir: "{app}\bin"; Flags: ignoreversion recursesubdirs skipifsourcedoesntexist

[Icons]
Name: "{group}\{#AppName}";            Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#AppIcon}"
Name: "{group}\Uninstall {#AppName}";  Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";      Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#AppIcon}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName} now"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent shellexec

[Code]
{ Returns True if `python` is resolvable on PATH. }
function PythonOnPath(): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec('cmd.exe', '/C where python', '', SW_HIDE, ewWaitUntilTerminated, ResultCode)
            and (ResultCode = 0);
end;

function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  if not PythonOnPath() then
  begin
    if MsgBox(
        'Python was not found on your PATH.' + #13#10#13#10 +
        'YT Audio Extractor needs Python 3.10 or newer. This installer does NOT' + #13#10 +
        'install Python. You can continue installing now and add Python later.' + #13#10#13#10 +
        'Open the Python download page in your browser now?',
        mbConfirmation, MB_YESNO) = IDYES then
      ShellExec('open', 'https://www.python.org/downloads/', '', '', SW_SHOW, ewNoWait, ResultCode);
  end;
end;
