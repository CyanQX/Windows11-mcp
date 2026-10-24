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
  PreviousDirectory: String;

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
  UninstallKey: String;
begin
  ManagerUpdate := ExpandConstant('{param:WMCPUPDATE|0}') = '1';
  Result := True;
  UninstallKey := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{B52D94AA-CB15-4BC1-BE81-8AD6A19A0C99}_is1';
  { Older managers launch the new package without switches. An existing
    installation goes directly to its progress page; the first install keeps
    the normal location/license wizard. Avoid relaunching the active setup,
    which Windows can deny during initialization. }
  if (not WizardSilent) and
     RegQueryStringValue(HKCU64, UninstallKey, 'InstallLocation', PreviousDirectory) and
     FileExists(AddBackslash(PreviousDirectory) + 'Windows-MCP.exe') then
    ManagerUpdate := True;
  if ManagerUpdate then
    Log('WMCP_UPDATE_MODE=progress-only');
end;

procedure InitializeWizard;
begin
  if ManagerUpdate then
  begin
    WizardForm.Caption := 'Windows-MCP 更新';
    if PreviousDirectory <> '' then
      WizardForm.DirEdit.Text := RemoveBackslashUnlessRoot(PreviousDirectory);
  end;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  { Silent mode already skips the interactive wizard internally. Skipping its
    initial/ready pages again prevents the engine from starting installation. }
  Result := ManagerUpdate and (not WizardSilent) and
    (PageID <> wpPreparing) and (PageID <> wpInstalling);
end;

procedure CurPageChanged(PageID: Integer);
begin
  if ManagerUpdate and (not WizardSilent) then
  begin
    WizardForm.BackButton.Visible := False;
    WizardForm.NextButton.Visible := False;
    { Inno always activates an initial/ready page even if ShouldSkipPage
      excludes it. Queue a normal click after the page transition finishes;
      a synchronous OnClick would be ignored while Inno is changing pages. }
    if (PageID <> wpPreparing) and (PageID <> wpInstalling) then
      PostMessage(WizardForm.NextButton.Handle, $00F5, 0, 0);
  end;
end;
