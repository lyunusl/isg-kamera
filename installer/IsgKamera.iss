; İSG Kamera installer (Inno Setup 6).
;
;   installer\build.cmd                     builds the exe and, if Inno Setup is installed, the installer too
;   ISCC.exe installer\IsgKamera.iss        installer only: dist\installer\IsgKamera-Kurulum-<version>.exe
;
; Installs step by step and also installs AugeLab Studio; the Studio window never opens:
;   1. İSG Kamera      per user (no administrator rights needed) under %LOCALAPPDATA%\Programs\IsgKamera
;   2. AugeLab Studio  if missing, downloads the official installer (SHA-256 verified); if the Visual C++ runtime is
;                      missing, downloads it from Microsoft and installs it after verifying its signature; installs
;                      Studio silently (otherwise the official setup window)
;   3. AugeLab account opens the account page in the browser (the license code is shown in the account)
;   4. License code    registers the code headless with Studio's own command (python -m studio verify); the code is not stored
; Steps 2-4 can be skipped: when the program starts, the setup wizard in the panel shows the same steps.
; The installer language (English or Turkish) is handed to the program on a fresh installation only (settings.json).
; Uninstalling removes the program; user data (%LOCALAPPDATA%\IsgKamera: cameras, events, clips) stays in place.

#define AppTitle "İSG Kamera"
#define AppVersionStr "0.1.0"
#ifndef Mode
  #define Mode "community"
#endif
#if Mode == "plain"
  #define SourceExe "..\dist\IsgKamera.exe"
#else
  #define SourceExe "..\dist\" + Mode + "\IsgKamera-" + Mode + ".exe"
#endif
; must match studio_setup.py
#define StudioUrl "https://studio-desktop-application.s3.eu-central-1.amazonaws.com/AugeLab%20Ai%20Studio.exe"
#define StudioSha256 "cec1e2613620c57fa51c853fc2b6148bc90f978eaf76e351b4b0b0f393681bd8"
#define VcUrl "https://aka.ms/vs/17/release/vc_redist.x64.exe"
#define AccountUrl "https://account.augelab.com/"
#define StudioExe "AugeLab AI Studio.exe"

[Setup]
AppId={{C19623DF-1091-4D49-AF8A-AFB6B3C34A53}
AppName={#AppTitle}
AppVersion={#AppVersionStr}
AppVerName={#AppTitle} {#AppVersionStr}
AppPublisher={#AppTitle}
VersionInfoVersion={#AppVersionStr}
VersionInfoProductName={#AppTitle}
VersionInfoDescription={#AppTitle} Setup
DefaultDirName={autopf}\IsgKamera
DefaultGroupName={#AppTitle}
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist\installer
; The asset name of v0.1.0 is kept: the README download link points to it.
OutputBaseFilename=IsgKamera-Kurulum-{#AppVersionStr}
SetupIconFile=isg-kamera.ico
UninstallDisplayIcon={app}\IsgKamera.exe
UninstallDisplayName={#AppTitle}
Compression=lzma2/fast
SolidCompression=no
WizardStyle=modern
ShowLanguageDialog=yes
; Windows Restart Manager cannot close the open panel (console window, it hangs): PrepareToInstall closes it.
CloseApplications=no
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"

[CustomMessages]
english.ShortcutsGroup=Shortcuts:
turkish.ShortcutsGroup=Kısayollar:
english.DesktopShortcut=Create a desktop shortcut
turkish.DesktopShortcut=Masaüstüne kısayol oluştur
english.OpenNow=Open {#AppTitle} now
turkish.OpenNow={#AppTitle} programını şimdi aç
english.DownloadFailed=%1 could not be downloaded: %2
turkish.DownloadFailed=%1 indirilemedi: %2
english.VcSignatureFailed=The Microsoft signature of the Visual C++ installer could not be verified; it was not run.
turkish.VcSignatureFailed=Visual C++ kurulum dosyasının Microsoft imzası doğrulanamadı; çalıştırılmadı.
english.VcInstalling=Installing the Visual C++ runtime. If Windows asks for permission, choose Yes.
turkish.VcInstalling=Visual C++ çalışma kitaplığı kuruluyor. Windows izin sorarsa Evet deyin.
english.VcInstallFailed=The Visual C++ runtime could not be installed (code %1).
turkish.VcInstallFailed=Visual C++ çalışma kitaplığı kurulamadı (kod %1).
english.StudioInstalling=Installing AugeLab Studio. This can take a few minutes; please wait…
turkish.StudioInstalling=AugeLab Studio kuruluyor. Birkaç dakika sürebilir; lütfen bekleyin…
english.SilentInstallIncomplete=The silent installation did not finish. The official AugeLab Studio setup window will open; complete the steps there.
turkish.SilentInstallIncomplete=Sessiz kurulum tamamlanmadı. AugeLab Studio'nun resmî kurulum penceresi açılacak; adımları orada tamamlayın.
english.StudioInstallFailed=AugeLab Studio could not be installed. You can try again with the setup wizard in the panel when you open the program.
turkish.StudioInstallFailed=AugeLab Studio kurulamadı. Programı açınca paneldeki kurulum sihirbazıyla yeniden deneyebilirsiniz.
english.LicenseSaving=Registering the license code with AugeLab Studio…
turkish.LicenseSaving=Lisans kodu AugeLab Studio'ya kaydediliyor…
english.LicenseSaveFailed=The license code could not be registered. Copy the code from your AugeLab account again and retry, or check your internet connection.
turkish.LicenseSaveFailed=Lisans kodu kaydedilemedi. Kodu AugeLab hesabınızdan yeniden kopyalayıp deneyin ya da internet bağlantınızı kontrol edin.
english.LicensePaste=Paste the code. It is only passed to AugeLab Studio; İSG Kamera does not store it.
turkish.LicensePaste=Kodu yapıştırın. Kod yalnızca AugeLab Studio'ya verilir; İSG Kamera onu saklamaz.
english.DownloadingFiles=Downloading the required files.
turkish.DownloadingFiles=Gerekli dosyalar indiriliyor.
english.StudioPageDescription=İSG Kamera analyzes video with AugeLab Studio on this computer.
turkish.StudioPageDescription=İSG Kamera görüntüleri bu bilgisayardaki AugeLab Studio ile analiz eder.
english.AccountPageCaption=AugeLab account
turkish.AccountPageCaption=AugeLab hesabı
english.AccountPageDescription=The license code that activates Studio is shown in your AugeLab account.
turkish.AccountPageDescription=Studio'yu etkinleştiren lisans kodu AugeLab hesabınızda görünür.
english.AccountPageText=If you do not have an AugeLab account, create one in your browser with the button below and confirm it with the link sent to your email. The free Community plan is enough.%n%nIf you already have an account, click Next.
turkish.AccountPageText=AugeLab hesabınız yoksa aşağıdaki düğmeyle tarayıcıda oluşturun ve e-postanıza gelen bağlantıyla onaylayın. Ücretsiz Community planı yeterlidir.%n%nHesabınız varsa İleri'ye basın.
english.CreateAccountButton=Create an AugeLab account ↗
turkish.CreateAccountButton=AugeLab hesabı oluştur ↗
english.LicensePageCaption=License code
turkish.LicensePageCaption=Lisans kodu
english.LicensePageDescription=AugeLab Studio is activated without opening its window.
turkish.LicensePageDescription=AugeLab Studio arayüz açılmadan etkinleştirilir.
english.LicenseField=License code (if you leave it empty, you can enter it later in the panel's setup wizard):
turkish.LicenseField=Lisans kodu (boş bırakırsanız sonra paneldeki sihirbazdan girersiniz):
english.GetLicenseButton=Get my license code ↗
turkish.GetLicenseButton=Lisans kodumu al ↗
english.StudioInstalled=AugeLab Studio is installed on this computer. Click Next.%n%nStudio runs in the background; İSG Kamera does not open its window.
turkish.StudioInstalled=AugeLab Studio bu bilgisayarda kurulu. İleri'ye basın.%n%nStudio arka planda çalışır; İSG Kamera onun penceresini açmaz.
english.StudioNotInstalled=AugeLab Studio is not installed on this computer. When you click Next:%n%n• the official AugeLab installer is downloaded and verified,%n• the Visual C++ component is installed from Microsoft if missing (Windows may ask for permission),%n• AugeLab Studio is installed silently for this user.%n%nStudio runs in the background; İSG Kamera does not open its window.
turkish.StudioNotInstalled=AugeLab Studio bu bilgisayarda kurulu değil. İleri'ye basınca:%n%n• AugeLab'in resmî kurulum dosyası indirilir ve doğrulanır,%n• Visual C++ bileşeni eksikse Microsoft'tan kurulur (Windows izin sorabilir),%n• AugeLab Studio bu kullanıcı için sessizce kurulur.%n%nStudio arka planda çalışır; İSG Kamera onun penceresini açmaz.
english.CloseFailed={#AppTitle} could not be closed. Close the program and start the installation again.
turkish.CloseFailed={#AppTitle} kapatılamadı. Programı kapatıp kurulumu yeniden başlatın.
english.ContinueWithoutStudio=Continue without installing AugeLab Studio? You can install it with the wizard in the panel when you open the program.
turkish.ContinueWithoutStudio=AugeLab Studio kurulmadan devam edilsin mi? Programı açınca paneldeki sihirbazla kurabilirsiniz.
english.ContinueWithoutLicense=Continue without entering a license code? You can enter it in the panel's wizard when you open the program.
turkish.ContinueWithoutLicense=Lisans kodu girmeden devam edilsin mi? Programı açınca paneldeki sihirbazdan girebilirsiniz.
english.LicenseInvalid=The license code does not look valid. Paste the code from your AugeLab account exactly as it is, without spaces.
turkish.LicenseInvalid=Lisans kodu geçersiz görünüyor. AugeLab hesabınızdaki kodu boşluksuz, olduğu gibi yapıştırın.

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopShortcut}"; GroupDescription: "{cm:ShortcutsGroup}"

[Files]
Source: "close-panel.ps1"; Flags: dontcopy
Source: "{#SourceExe}"; DestDir: "{app}"; DestName: "IsgKamera.exe"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\ATTRIBUTIONS.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\MODEL-LICENSES.md"; DestDir: "{app}"; Flags: ignoreversion
#if Mode == "community"
Source: "community-models\RUST-NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion
#endif

[Icons]
Name: "{autoprograms}\{#AppTitle}"; Filename: "{app}\IsgKamera.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppTitle}"; Filename: "{app}\IsgKamera.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\IsgKamera.exe"; Description: "{cm:OpenNow}"; Flags: nowait postinstall skipifsilent

[Messages]
english.FinishedLabel=Setup has finished. When {#AppTitle} opens, you can add your cameras. AugeLab Studio runs in the background; its window does not open.
turkish.FinishedLabel=Kurulum tamamlandı. {#AppTitle} açıldığında kameralarınızı ekleyebilirsiniz. AugeLab Studio arka planda çalışır; penceresi açılmaz.

[Code]
var
  StudioPage: TOutputMsgWizardPage;
  AccountPage: TOutputMsgWizardPage;
  LicensePage: TInputQueryWizardPage;
  DownloadPage: TDownloadWizardPage;
  StudioJustInstalled: Boolean;

function StudioDir(): String;
begin
  Result := ExpandConstant('{localappdata}\Programs\AugeLab Studio');
end;

function StudioComplete(Dir: String): Boolean;
begin
  Result := FileExists(Dir + '\{#StudioExe}') and FileExists(Dir + '\Python\python.exe');
end;

{ Where Studio is: the default folder, otherwise InstallLocation from the Windows "Installed apps" registry entries. }
function StudioLocation(): String;
var
  Roots: array of Integer;
  Names: TArrayOfString;
  I, K: Integer;
  Key, Name, Location: String;
begin
  Result := '';
  if StudioComplete(StudioDir()) then begin
    Result := StudioDir();
    exit;
  end;
  SetArrayLength(Roots, 2);
  Roots[0] := HKCU;
  Roots[1] := HKLM64;
  for K := 0 to 1 do begin
    Key := 'Software\Microsoft\Windows\CurrentVersion\Uninstall';
    if RegGetSubkeyNames(Roots[K], Key, Names) then
      for I := 0 to GetArrayLength(Names) - 1 do
        if RegQueryStringValue(Roots[K], Key + '\' + Names[I], 'DisplayName', Name) and
           (Pos('augelab', Lowercase(Name)) > 0) and (Pos('studio', Lowercase(Name)) > 0) and
           RegQueryStringValue(Roots[K], Key + '\' + Names[I], 'InstallLocation', Location) then begin
          Location := RemoveQuotes(Location);
          if (Location <> '') and StudioComplete(Location) then begin
            Result := Location;
            exit;
          end;
        end;
  end;
end;

function VcInstalled(): Boolean;
var
  Value: Cardinal;
begin
  Result := (RegQueryDWordValue(HKLM64, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Installed', Value) and (Value = 1)) or
            (RegQueryDWordValue(HKLM32, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Installed', Value) and (Value = 1));
end;

function MicrosoftSigned(FileName: String): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec('powershell.exe', '-NoProfile -NonInteractive -Command "$s = Get-AuthenticodeSignature -LiteralPath ''' + FileName +
    '''; if ($s.Status -eq ''Valid'' -and $s.SignerCertificate.Subject -match ''O=Microsoft Corporation'') { exit 0 } else { exit 1 }"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

procedure CloseStudioWindow();
var
  ResultCode, I: Integer;
begin
  { If the official installer opens Studio by itself when it finishes, close it: İSG Kamera uses Studio headless. }
  for I := 1 to 8 do begin
    Sleep(1000);
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM "{#StudioExe}" /T', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;
end;

function Download(Url, Name, Sha256: String): Boolean;
begin
  DownloadPage.Clear;
  DownloadPage.Add(Url, Name, Sha256);
  DownloadPage.Show;
  try
    try
      DownloadPage.Download;
      Result := True;
    except
      Result := False;
      if not DownloadPage.AbortedByUser then
        MsgBox(FmtMessage(CustomMessage('DownloadFailed'), [Name, GetExceptionMessage]), mbError, MB_OK);
    end;
  finally
    DownloadPage.Hide;
  end;
end;

function InstallStudio(): Boolean;
var
  Installer, Vc: String;
  ResultCode: Integer;
begin
  Result := False;
  if not VcInstalled() then begin
    if not Download('{#VcUrl}', 'vc_redist.x64.exe', '') then exit;
    Vc := ExpandConstant('{tmp}\vc_redist.x64.exe');
    if not MicrosoftSigned(Vc) then begin
      MsgBox(CustomMessage('VcSignatureFailed'), mbError, MB_OK);
      exit;
    end;
    StudioPage.MsgLabel.Caption := CustomMessage('VcInstalling');
    if not ShellExec('', Vc, '/install /passive /norestart', '', SW_SHOW, ewWaitUntilTerminated, ResultCode) or
       not ((ResultCode = 0) or (ResultCode = 1638) or (ResultCode = 3010)) or not VcInstalled() then begin
      MsgBox(FmtMessage(CustomMessage('VcInstallFailed'), [IntToStr(ResultCode)]), mbError, MB_OK);
      exit;
    end;
  end;
  if not Download('{#StudioUrl}', 'AugeLab-Studio-Setup.exe', '{#StudioSha256}') then exit;
  Installer := ExpandConstant('{tmp}\AugeLab-Studio-Setup.exe');
  StudioPage.MsgLabel.Caption := CustomMessage('StudioInstalling');
  WizardForm.NextButton.Enabled := False;
  try
    ShellExec('', Installer, '--silent --install-dir "' + StudioDir() + '" --start-menu-shortcut', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    if StudioLocation() = '' then begin
      MsgBox(CustomMessage('SilentInstallIncomplete'), mbInformation, MB_OK);
      ShellExec('', Installer, '', '', SW_SHOW, ewWaitUntilTerminated, ResultCode);
    end;
  finally
    WizardForm.NextButton.Enabled := True;
  end;
  Result := StudioLocation() <> '';
  if Result then begin
    StudioJustInstalled := True;
    CloseStudioWindow();
  end else
    MsgBox(CustomMessage('StudioInstallFailed'), mbError, MB_OK);
end;

function IsValidCode(Code: String): Boolean;
var
  I: Integer;
  C: Char;
begin
  Result := (Length(Code) >= 6) and (Length(Code) <= 1024);
  for I := 1 to Length(Code) do begin
    C := Code[I];
    if not (((C >= 'A') and (C <= 'Z')) or ((C >= 'a') and (C <= 'z')) or ((C >= '0') and (C <= '9')) or
            ((I > 1) and ((C = '.') or (C = '_') or (C = '+') or (C = '/') or (C = '=') or (C = '-')))) then
      Result := False;
  end;
end;

function SetEnvVar(Name, Value: String): Boolean;
  external 'SetEnvironmentVariableW@kernel32.dll stdcall';
function ClearEnvVar(Name: String; Value: Cardinal): Boolean;
  external 'SetEnvironmentVariableW@kernel32.dll stdcall';

function SaveLicense(Code: String): Boolean;
var
  Location, Marker: String;
  ExitCode: Integer;
  Ran: Boolean;
begin
  Result := False;
  Location := StudioLocation();
  WizardForm.NextButton.Enabled := False;
  LicensePage.SubCaptionLabel.Caption := CustomMessage('LicenseSaving');
  try
    { The code is only given to Studio's own "verify" command, through an environment variable so it does not show up
      on the command line (same as studio_setup.VERIFY). It is never written to a file or a log. }
    SetEnvVar('ISG_LICENSE_CODE', Code);
    try
      Ran := Exec(Location + '\Python\python.exe', '-c "import os,runpy,sys; sys.argv=[''studio'',''verify'',' +
        'os.environ.pop(''ISG_LICENSE_CODE'','''')]; runpy.run_module(''studio'',run_name=''__main__'',alter_sys=True)"',
        Location, SW_HIDE, ewWaitUntilTerminated, ExitCode);
    finally
      ClearEnvVar('ISG_LICENSE_CODE', 0);
    end;
    if Ran and (ExitCode = 0) then begin
      Marker := ExpandConstant('{localappdata}\IsgKamera');
      ForceDirectories(Marker);
      SaveStringToFile(Marker + '\studio-license.txt', GetDateTimeString('yyyy-mm-dd hh:nn:ss', '-', ':'), False);
      Result := True;
    end else
      MsgBox(CustomMessage('LicenseSaveFailed'), mbError, MB_OK);
  finally
    WizardForm.NextButton.Enabled := True;
    LicensePage.SubCaptionLabel.Caption := CustomMessage('LicensePaste');
  end;
end;

procedure OpenAccount(Sender: TObject);
var
  ResultCode: Integer;
begin
  ShellExecAsOriginalUser('open', '{#AccountUrl}?p=signup', '', '', SW_SHOWNORMAL, ewNoWait, ResultCode);
end;

procedure GetCode(Sender: TObject);
var
  ResultCode: Integer;
begin
  ShellExecAsOriginalUser('open', '{#AccountUrl}', '', '', SW_SHOWNORMAL, ewNoWait, ResultCode);
end;

function MakeButton(Page: TWizardPage; Caption: String; Top: Integer; OnClick: TNotifyEvent): TNewButton;
begin
  Result := TNewButton.Create(Page);
  Result.Parent := Page.Surface;
  Result.Caption := Caption;
  Result.Top := Top;
  Result.Left := 0;
  Result.Width := ScaleX(220);
  Result.Height := ScaleY(28);
  Result.OnClick := OnClick;
end;

procedure InitializeWizard();
begin
  DownloadPage := CreateDownloadPage('AugeLab Studio', CustomMessage('DownloadingFiles'), nil);
  StudioPage := CreateOutputMsgPage(wpInstalling, 'AugeLab Studio', CustomMessage('StudioPageDescription'), '');
  AccountPage := CreateOutputMsgPage(StudioPage.ID, CustomMessage('AccountPageCaption'),
    CustomMessage('AccountPageDescription'), FmtMessage(CustomMessage('AccountPageText'), []));
  MakeButton(AccountPage, CustomMessage('CreateAccountButton'), ScaleY(110), @OpenAccount);
  LicensePage := CreateInputQueryPage(AccountPage.ID, CustomMessage('LicensePageCaption'),
    CustomMessage('LicensePageDescription'), CustomMessage('LicensePaste'));
  LicensePage.Add(CustomMessage('LicenseField'), False);
  MakeButton(LicensePage, CustomMessage('GetLicenseButton'), LicensePage.Edits[0].Top + LicensePage.Edits[0].Height + ScaleY(14), @GetCode);
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = StudioPage.ID then begin
    if StudioLocation() <> '' then
      StudioPage.MsgLabel.Caption := FmtMessage(CustomMessage('StudioInstalled'), [])
    else
      StudioPage.MsgLabel.Caption := FmtMessage(CustomMessage('StudioNotInstalled'), []);
  end;
end;

{ Hands the installer language to the program, on a fresh installation only: when the per-user data folder does not
  exist yet, it is created with a settings.json whose only field is "language": "en" or "tr" (UTF-8, no BOM; ASCII
  text). An existing folder or file (settings.json, or genel.json of v0.1.0) is never touched, so existing
  installations keep their language. }
procedure PassLanguage();
var
  DataDir, Lang: String;
begin
  DataDir := ExpandConstant('{localappdata}\IsgKamera');
  if DirExists(DataDir) or FileExists(DataDir + '\settings.json') or FileExists(DataDir + '\genel.json') then exit;
  if ActiveLanguage() = 'turkish' then
    Lang := 'tr'
  else
    Lang := 'en';
  if ForceDirectories(DataDir) then
    SaveStringToFile(DataDir + '\settings.json', '{"language": "' + Lang + '"}', False);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    PassLanguage();
end;

{ On an update, close the panel if it is open (so its file is not left in use). }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  { Only the İSG Kamera in this folder is closed. If the script cannot run, setup stops. }
  ExtractTemporaryFile('close-panel.ps1');
  if not Exec('powershell.exe', '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
              ExpandConstant('{tmp}\close-panel.ps1') + '" -AppPath "' + ExpandConstant('{app}\IsgKamera.exe') + '"',
              '', SW_HIDE, ewWaitUntilTerminated, ResultCode) or (ResultCode <> 0) then
    Result := CustomMessage('CloseFailed');
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  { If Studio could not be installed, the account and license steps are left to the wizard in the program. }
  Result := ((PageID = AccountPage.ID) or (PageID = LicensePage.ID)) and (StudioLocation() = '');
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Code: String;
begin
  Result := True;
  if (CurPageID = StudioPage.ID) and (StudioLocation() = '') then begin
    if not InstallStudio() then
      Result := MsgBox(CustomMessage('ContinueWithoutStudio'), mbConfirmation, MB_YESNO) = IDYES;
  end else if CurPageID = LicensePage.ID then begin
    Code := Trim(LicensePage.Values[0]);
    if Code = '' then
      Result := MsgBox(CustomMessage('ContinueWithoutLicense'), mbConfirmation, MB_YESNO) = IDYES
    else if not IsValidCode(Code) then begin
      MsgBox(CustomMessage('LicenseInvalid'), mbError, MB_OK);
      Result := False;
    end else begin
      Result := SaveLicense(Code);
      if Result then LicensePage.Values[0] := '';
    end;
  end;
end;
