#ifndef BundleDir
  #error BundleDir is required
#endif
#ifndef ArtifactDir
  #error ArtifactDir is required
#endif
#ifndef BuildDate
  #error BuildDate is required
#endif
#ifndef BuildVersion
  #error BuildVersion is required
#endif

[Setup]
AppId=SpectraAnalyzer.Desktop.Standalone
AppName=Spectra Analyzer
AppVersion={#BuildVersion}
VersionInfoVersion={#BuildVersion}
AppPublisher=Spectra Analyzer
DefaultDirName={localappdata}\Programs\SpectraAnalyzerStandalone
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#ArtifactDir}
OutputBaseFilename=SpectraAnalyzer-{#BuildVersion}-Setup
SetupIconFile=..\..\spectra_app\resources\spectra_analyzer.ico
UninstallDisplayIcon={app}\SpectraAnalyzer.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll,*.pyd
RestartApplications=no
UsePreviousTasks=yes

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; Flags: checkedonce

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "standalone-owner.ini"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\Spectra Analyzer (Standalone)"; Filename: "{app}\SpectraAnalyzer.exe"; WorkingDir: "{app}"; IconFilename: "{app}\SpectraAnalyzer.exe"; AppUserModelID: "SpectraAnalyzer.Desktop"
Name: "{userdesktop}\Spectra Analyzer (Standalone)"; Filename: "{app}\SpectraAnalyzer.exe"; WorkingDir: "{app}"; IconFilename: "{app}\SpectraAnalyzer.exe"; AppUserModelID: "SpectraAnalyzer.Desktop"; Tasks: desktopicon

[Run]
Filename: "{app}\SpectraAnalyzer.exe"; Description: "Open Spectra Analyzer"; Flags: nowait postinstall skipifsilent

[Code]
function DirectoryHasContents(const Directory: String): Boolean;
var
  Entry: TFindRec;
begin
  Result := False;
  if FindFirst(AddBackslash(Directory) + '*', Entry) then
  begin
    try
      repeat
        if (Entry.Name <> '.') and (Entry.Name <> '..') then
        begin
          Result := True;
          Break;
        end;
      until not FindNext(Entry);
    finally
      FindClose(Entry);
    end;
  end;
end;

function ShortcutIsAvailable(const Filename, Target: String): Boolean;
var
  Shell, Link: Variant;
begin
  Result := not FileExists(Filename);
  if not Result then
  begin
    try
      Shell := CreateOleObject('WScript.Shell');
      Link := Shell.CreateShortcut(Filename);
      Result := CompareText(Link.TargetPath, Target) = 0;
    except
      Result := False;
    end;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Target, Owner, Executable: String;
begin
  Result := '';
  Target := ExpandConstant('{app}');
  if DirExists(Target) and DirectoryHasContents(Target) then
  begin
    Owner := GetIniString('Installation', 'AppId', '',
      AddBackslash(Target) + 'standalone-owner.ini');
    if Owner <> 'SpectraAnalyzer.Desktop.Standalone' then
    begin
      Result := 'The selected folder contains other files. Choose an empty folder ' +
        'or an existing Spectra Analyzer standalone installation.';
      Exit;
    end;
  end;
  if not WizardNoIcons then
  begin
    Executable := AddBackslash(Target) + 'SpectraAnalyzer.exe';
    if not ShortcutIsAvailable(ExpandConstant('{userprograms}\Spectra Analyzer (Standalone).lnk'), Executable) then
      Result := 'A Start menu shortcut belongs to another installation. Update that installation in its original folder.';
    if WizardIsTaskSelected('desktopicon') and
      not ShortcutIsAvailable(ExpandConstant('{userdesktop}\Spectra Analyzer (Standalone).lnk'), Executable) then
      Result := 'A desktop shortcut belongs to another installation. Update that installation in its original folder.';
  end;
end;

// No [UninstallDelete] entries: only installer-owned files/shortcuts are removed.
// Project files and the application's HKCU settings are intentionally preserved.
