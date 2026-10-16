; Build with scripts/build_windows_installer.py. Only manager files are owned
; by this uninstaller; Skill/MCP integrations are maintained in the manager UI.
[Setup]
AppId={{B52D94AA-CB15-4BC1-BE81-8AD6A19A0C99}
AppName=Windows-MCP
AppVersion={#AppVersion}
AppPublisher=CyanQX
AppPublisherURL=https://github.com/CyanQX/Windows11-mcp
AppSupportURL=https://github.com/CyanQX/Windows11-mcp/issues
DefaultDirName={localappdata}\Programs\Windows-MCP
DefaultGroupName=Windows-MCP
DisableDirPage=no
DisableProgramGroupPage=yes
AllowNoIcons=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=Windows-MCP-Setup-v{#AppVersion}-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ShowLanguageDialog=no
UninstallDisplayIcon={app}\Windows-MCP.exe
UninstallDisplayName=Windows-MCP {#AppVersion}
CloseApplications=yes
RestartApplications=no
LicenseFile={#RepositoryRoot}\LICENSE.md
SetupLogging=yes

[Languages]
Name: "chinesesimp"; MessagesFile: "{#RepositoryRoot}\installer\resources\inno-languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; Flags: unchecked

[Files]
Source: "{#PayloadPath}"; DestDir: "{app}"; DestName: "Windows-MCP.exe"; Flags: ignoreversion
Source: "{#RepositoryRoot}\LICENSE.md"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "{#RepositoryRoot}\installer\resources\third-party-licenses.txt"; DestDir: "{app}"; DestName: "THIRD-PARTY-LICENSES.txt"; Flags: ignoreversion

[Icons]
Name: "{group}\Windows-MCP"; Filename: "{app}\Windows-MCP.exe"
Name: "{group}\Uninstall Windows-MCP"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Windows-MCP"; Filename: "{app}\Windows-MCP.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Windows-MCP.exe"; Description: "{cm:LaunchProgram,Windows-MCP}"; Flags: nowait postinstall skipifsilent; Check: not IsManagerUpdate
Filename: "{app}\Windows-MCP.exe"; Parameters: "--finish-installer-update"; Flags: nowait; Check: ShouldLaunchUpdate

[Code]
var
  ManagerUpdate: Boolean;

function IsManagerUpdate: Boolean;
begin
  Result := ManagerUpdate;
end;

function ShouldLaunchUpdate: Boolean;
begin
  Result := ManagerUpdate and (ExpandConstant('{param:WMCPNOLAUNCH|0}') <> '1');
end;

function InitializeSetup: Boolean;
var
  PreviousDirectory: String;
  Parameters: String;
  LogFile: String;
  ExitCode: Integer;
  UninstallKey: String;
begin
  ManagerUpdate := ExpandConstant('{param:WMCPUPDATE|0}') = '1';
  if ManagerUpdate then
    Log('WMCP_UPDATE_MODE=progress-only');
  Result := True;
  UninstallKey := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{B52D94AA-CB15-4BC1-BE81-8AD6A19A0C99}_is1';
  { Older managers launch the new package without switches. An existing
    installation goes directly to its progress page; the first install keeps
    the normal location/license wizard. Never force-close applications. }
  if (not WizardSilent) and
     RegQueryStringValue(HKCU64, UninstallKey, 'InstallLocation', PreviousDirectory) and
     FileExists(AddBackslash(PreviousDirectory) + 'Windows-MCP.exe') then
  begin
    Parameters := '/SILENT /SUPPRESSMSGBOXES /SP- /NORESTART /WMCPUPDATE=1 /DIR="' + PreviousDirectory + '"' +
      ' /WMCPNOLAUNCH=' + ExpandConstant('{param:WMCPNOLAUNCH|0}');
    LogFile := ExpandConstant('{param:LOG|}');
    if LogFile <> '' then
      Parameters := Parameters + ' /LOG="' + LogFile + '.update"';
    if Exec(ExpandConstant('{srcexe}'), Parameters,
      '', SW_SHOWNORMAL, ewNoWait, ExitCode) then
      Result := False
    else
      RaiseException('Unable to start the update installer. The current version has been preserved.');
  end;
end;
