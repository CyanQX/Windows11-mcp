; Build with scripts/build_windows_installer.py. Only manager files are owned
; by this uninstaller; Skill/MCP integrations are maintained in the manager UI.
[Setup]
AppId={{B52D94AA-CB15-4BC1-BE81-8AD6A19A0C99}
AppName={#ProductName}
AppVerName={#ProductName} {#AppVersion}
SetupMutex=Actilo.Setup
AppVersion={#AppVersion}
AppPublisher=CyanQX
AppPublisherURL=https://github.com/CyanQX/Windows11-mcp
AppSupportURL=https://github.com/CyanQX/Windows11-mcp/issues
DefaultDirName={code:DefaultInstallDirectory}
UsePreviousAppDir=no
DefaultGroupName={#ProductName}
DisableDirPage=no
DisableProgramGroupPage=yes
AllowNoIcons=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename={#SetupBase}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ShowLanguageDialog=no
UninstallDisplayIcon={app}\Windows-MCP.exe
UninstallDisplayName={#ProductName} {#AppVersion}
CloseApplications=yes
RestartApplications=no
LicenseFile={#RepositoryRoot}\LICENSE.md
SetupLogging=yes
SetupIconFile={#RepositoryRoot}\installer\resources\actilo.ico

[Languages]
Name: "chinesesimp"; MessagesFile: "{#RepositoryRoot}\installer\resources\inno-languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
SetupAppTitle=Actilo Setup
SetupWindowTitle=Actilo Setup

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"

[Files]
#if FileExists(ExtractFileDir(PayloadPath) + "\WindowsMCP.bundle.json")
Source: "{#ExtractFileDir(PayloadPath)}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
#else
Source: "{#PayloadPath}"; DestDir: "{app}"; DestName: "Windows-MCP.exe"; Flags: ignoreversion
#endif
Source: "{#RepositoryRoot}\LICENSE.md"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "{#RepositoryRoot}\installer\resources\third-party-licenses.txt"; DestDir: "{app}"; DestName: "THIRD-PARTY-LICENSES.txt"; Flags: ignoreversion

[Icons]
Name: "{group}\Actilo"; Filename: "{app}\Windows-MCP.exe"
Name: "{group}\卸载 Actilo"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Actilo"; Filename: "{app}\Windows-MCP.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Windows-MCP.exe"; Description: "安装完成后启动 Actilo"; Flags: nowait postinstall runasoriginaluser skipifsilent; Check: not IsManagerUpdate
Filename: "{app}\Windows-MCP.exe"; Parameters: "--finish-installer-update"; Flags: nowait runasoriginaluser; Check: ShouldLaunchUpdate

[Code]
function SetEnvironmentVariable(lpName, lpValue: String): Boolean;
  external 'SetEnvironmentVariableW@kernel32.dll stdcall';
function CreateProbeFile(FileName: String; DesiredAccess, ShareMode: Cardinal;
  SecurityAttributes: Integer; CreationDisposition, FlagsAndAttributes: Cardinal;
  TemplateFile: THandle): THandle;
  external 'CreateFileW@kernel32.dll stdcall';
function CloseProbeFile(Handle: THandle): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';

var
  ManagerUpdate: Boolean;
  LegacyInstallation: Boolean;
  PreviousDirectory: String;
  PreflightExecutable, TransactionPath: String;
  InstallationVerified: Boolean;

function IsManagerUpdate: Boolean;
begin
  Result := ManagerUpdate;
end;

function ShouldLaunchUpdate: Boolean;
begin
  Result := ManagerUpdate and (ExpandConstant('{param:WMCPNOLAUNCH|0}') <> '1');
end;

function IsLegacyVersion(Value: String): Boolean;
var
  Major, Minor, Dot: Integer;
begin
  Dot := Pos('.', Value);
  Major := StrToIntDef(Copy(Value, 1, Dot - 1), 0);
  Delete(Value, 1, Dot);
  Dot := Pos('.', Value);
  if Dot = 0 then Dot := Length(Value) + 1;
  Minor := StrToIntDef(Copy(Value, 1, Dot - 1), 0);
  Result := (Major < 1) or ((Major = 1) and (Minor < 7));
end;

function InitializeSetup: Boolean;
var
  UninstallKey, PreviousVersion: String;
  BrowserCode: Integer;
begin
  { Also repairs launches from pre-1.8 managers whose environment refers to an
    extraction directory that disappears when the old manager exits. }
  SetEnvironmentVariable('PYINSTALLER_RESET_ENVIRONMENT', '1');
  ManagerUpdate := ExpandConstant('{param:WMCPUPDATE|0}') = '1';
  Result := True;
  UninstallKey := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{B52D94AA-CB15-4BC1-BE81-8AD6A19A0C99}_is1';
  LegacyInstallation := RegQueryStringValue(HKCU64, UninstallKey, 'DisplayVersion', PreviousVersion) and IsLegacyVersion(PreviousVersion);
  if LegacyInstallation and (ManagerUpdate or WizardSilent) and
     (ExpandConstant('{param:ACTILOMANUALMIGRATION|0}') <> '1') then
  begin
    if MsgBox('发现重大版本更新：Actilo v{#AppVersion}' + #13#10 +
        '当前版本低于 v1.7.0，不能静默原地升级。请从 GitHub 下载 Actilo_{#AppVersion}_setup.exe 并手动安装。' + #13#10 +
        '选择“是”前往 GitHub 下载；选择“否”稍后再说。', mbInformation, MB_YESNO) = IDYES then
      ShellExec('open', 'https://github.com/CyanQX/Windows11-mcp/releases/latest', '', '', SW_SHOWNORMAL, ewNoWait, BrowserCode);
    Result := False;
    Exit;
  end;
  if LegacyInstallation then
    ManagerUpdate := False;
  { Older managers launch the new package without switches. An existing
    installation goes directly to its progress page; the first install keeps
    the normal location/license wizard. Avoid relaunching the active setup,
    which Windows can deny during initialization. }
  if (not WizardSilent) and
     RegQueryStringValue(HKCU64, UninstallKey, 'InstallLocation', PreviousDirectory) and
     FileExists(AddBackslash(PreviousDirectory) + 'Windows-MCP.exe') and (not LegacyInstallation) then
    ManagerUpdate := True;
  if ManagerUpdate then
    Log('WMCP_UPDATE_MODE=progress-only');
end;

procedure InitializeWizard;
begin
  if ManagerUpdate then
  begin
    WizardForm.Caption := 'Actilo 更新';
    if PreviousDirectory <> '' then
      WizardForm.DirEdit.Text := RemoveBackslashUnlessRoot(PreviousDirectory);
  end;
end;

function DefaultInstallDirectory(Param: String): String;
var
  FreeBytes, TotalBytes: Int64;
  Probe, Candidate: String;
  Handle: THandle;
begin
  Result := ExpandConstant('{localappdata}\Programs\Actilo');
  if (PreviousDirectory <> '') and (not LegacyInstallation) then
  begin
    Result := RemoveBackslashUnlessRoot(PreviousDirectory);
    Exit;
  end;
  if DirExists('D:\') and GetSpaceOnDisk64('D:\', FreeBytes, TotalBytes) and
     (FreeBytes > 2147483648) then
  begin
    Candidate := 'D:\Actilo';
    if FileExists(Candidate) then Exit;
    try
      if DirExists(Candidate) then
        Probe := AddBackslash(Candidate)
      else
        Probe := 'D:\';
      Probe := Probe + '.actilo-probe-' + Copy(GetSHA256OfUnicodeString(ExpandConstant('{tmp}')), 1, 24) + '.tmp';
      { CREATE_NEW never touches an existing file; DELETE_ON_CLOSE removes our
        probe even if Setup is interrupted. Inno does not export GetTempFileName. }
      Handle := CreateProbeFile(Probe, $40000000, 0, 0, 1, $04000100, 0);
      if Handle <> -1 then
      begin
        CloseProbeFile(Handle);
        Result := Candidate;
      end;
    except
      Log('ACTILO_DEFAULT_DIR=local-appdata-fallback');
    end;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
  Raw: AnsiString;
  ResultFile: String;
begin
  Result := '';
  if TransactionPath <> '' then
    Exit;
  try
    WizardForm.StatusLabel.Caption := '正在备份并检查旧安装…';
    ExtractTemporaryFiles('{app}\*');
    PreflightExecutable := ExpandConstant('{tmp}') + '\{app}\Windows-MCP.exe';
    ResultFile := ExpandConstant('{tmp}\actilo-transaction.txt');
    if (not Exec(PreflightExecutable, '--installer-prepare="' + ExpandConstant('{app}') +
        '" --installer-result="' + ResultFile + '"', '', SW_HIDE, ewWaitUntilTerminated, Code)) or (Code <> 0) then
    begin
      Result := 'Actilo 安装预检查失败，未继续安装。原文件与可恢复备份已保留。请查看安装日志。';
      Exit;
    end;
    if not LoadStringFromFile(ResultFile, Raw) then
    begin
      Result := '无法读取安装备份事务，已中止。';
      Exit;
    end;
    TransactionPath := Trim(UTF8Decode(Raw));
    Log('ACTILO_TRANSACTION=' + TransactionPath);
    Log('ACTILO_PREFLIGHT=PASS');
  except
    Result := GetExceptionMessage;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Code: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    WizardForm.StatusLabel.Caption := '正在同步并验证 Skill、MCP 与 Actilo…';
    if (TransactionPath = '') or (not Exec(PreflightExecutable,
        '--installer-commit="' + TransactionPath + '"', '', SW_HIDE, ewWaitUntilTerminated, Code)) or (Code <> 0) then
      RaiseException('Actilo 验证未完成，安装没有标记为成功；受管文件正在恢复，备份已保留。');
    InstallationVerified := True;
    Log('WMCP_INSTALLED_INTEGRATIONS_SYNC=PASS');
    Log('ACTILO_INSTALL_VERIFIED=PASS');
  end;
end;

procedure DeinitializeSetup;
var
  Code: Integer;
begin
  if (TransactionPath <> '') and (not InstallationVerified) then
    if not Exec(PreflightExecutable, '--installer-rollback="' + TransactionPath + '"',
        '', SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then
      Log('ACTILO_RECOVERY_REQUIRED=' + TransactionPath);
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
