; İSG Kamera kurulum dosyası (Inno Setup 6).
;
;   kurulum\derle.cmd                       exe'yi derler, Inno Setup kuruluysa kurulum dosyasını da üretir
;   ISCC.exe kurulum\IsgKamera.iss          yalnızca kurulum dosyası: dist\kurulum\IsgKamera-Kurulum-<sürüm>.exe
;
; Kullanıcı başına kurar (yönetici izni gerekmez), AugeLab Studio gibi %LOCALAPPDATA%\Programs altına. Başlat menüsüne
; (isterse masaüstüne) kısayol koyar, Windows'un "Yüklü uygulamalar" listesinden kaldırılabilir. Kaldırınca program
; silinir; kullanıcı verisi (%LOCALAPPDATA%\IsgKamera: kameralar, olaylar, klipler) yerinde kalır.
; AugeLab Studio'yu kurmaz: program ilk açılışta Studio yoksa paneldeki sihirbazla kurar.

#define Ad "İSG Kamera"
#define Surum "0.1.0"
#ifndef Kip
  #define Kip "community"
#endif
#if Kip == "duz"
  #define Kaynak "..\dist\IsgKamera.exe"
#else
  #define Kaynak "..\dist\" + Kip + "\IsgKamera-" + Kip + ".exe"
#endif

[Setup]
AppId={{C19623DF-1091-4D49-AF8A-AFB6B3C34A53}
AppName={#Ad}
AppVersion={#Surum}
AppVerName={#Ad} {#Surum}
AppPublisher={#Ad}
VersionInfoVersion={#Surum}
VersionInfoProductName={#Ad}
VersionInfoDescription={#Ad} kurulumu
DefaultDirName={autopf}\IsgKamera
DefaultGroupName={#Ad}
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist\kurulum
OutputBaseFilename=IsgKamera-Kurulum-{#Surum}
SetupIconFile=isg-kamera.ico
UninstallDisplayIcon={app}\IsgKamera.exe
UninstallDisplayName={#Ad}
Compression=lzma2/fast
SolidCompression=no
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "tr"; MessagesFile: "compiler:Languages\Turkish.isl"

[Tasks]
Name: "masaustu"; Description: "Masaüstüne kısayol oluştur"; GroupDescription: "Kısayollar:"

[Files]
Source: "{#Kaynak}"; DestDir: "{app}"; DestName: "IsgKamera.exe"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\ATIFLAR.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\MODEL-LISANSLARI.md"; DestDir: "{app}"; Flags: ignoreversion
#if Kip == "community"
Source: "community-modeller\RUST-NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion
#endif

[Icons]
Name: "{autoprograms}\{#Ad}"; Filename: "{app}\IsgKamera.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\{#Ad}"; Filename: "{app}\IsgKamera.exe"; WorkingDir: "{app}"; Tasks: masaustu

[Run]
Filename: "{app}\IsgKamera.exe"; Description: "{#Ad} programını şimdi aç"; Flags: nowait postinstall skipifsilent

[Messages]
tr.FinishedLabel=Kurulum tamamlandı. {#Ad} açıldığında AugeLab Studio bu bilgisayarda yoksa panel onu adım adım kurar.
