; Inno Setup script: ValhallISC-<version>-windows-x86_64-setup.exe (ADR-016).
; Built by the Windows workflow after PyInstaller:  iscc /DAppVersion=0.9.3 packaging\windows\valhallisc.iss
; Per-user install (no administrator rights): %LOCALAPPDATA%\Programs\ValhallISC, a Start menu entry, and an
; entry in Settings > Apps > Installed apps to uninstall it. WinFsp is a prerequisite and is not bundled.

#ifndef AppVersion
  #error Pass the version: iscc /DAppVersion=x.y.z
#endif

[Setup]
; Never change AppId: Windows recognises updates and the uninstaller by it.
AppId={{2DAE1885-8CC3-5321-A654-A9743821F73A}
AppName=ValhallISC
AppVersion={#AppVersion}
AppVerName=ValhallISC {#AppVersion}
AppPublisher=ValhallISC
AppPublisherURL=https://github.com/m0nt0/ValhallISC
AppSupportURL=https://github.com/m0nt0/ValhallISC/issues
AppUpdatesURL=https://github.com/m0nt0/ValhallISC/releases
DefaultDirName={autopf}\ValhallISC
DefaultGroupName=ValhallISC
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\..\LICENSE
SetupIconFile=..\..\assets\valhallisc.ico
UninstallDisplayIcon={app}\ValhallISC.exe
UninstallDisplayName=ValhallISC
OutputDir=..\..\dist
OutputBaseFilename=ValhallISC-{#AppVersion}-windows-x86_64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; The running app holds its executable open: close it before updating or uninstalling.
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\..\dist\ValhallISC.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\dist\valhallisc-cli.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\ValhallISC"; Filename: "{app}\ValhallISC.exe"; Comment: "Mount InterSystems IRIS servers as folders"
Name: "{autodesktop}\ValhallISC"; Filename: "{app}\ValhallISC.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ValhallISC.exe"; Description: "{cm:LaunchProgram,ValhallISC}"; Flags: nowait postinstall skipifsilent
