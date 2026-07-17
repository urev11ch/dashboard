; Inno Setup script for OptiCIP Dashboard.
; Builds installer_out\OptiCIP-Dashboard-Setup.exe from dist\OptiCIP-Dashboard.exe.
; Installs the app, creates shortcuts and silently installs the Microsoft Edge
; WebView2 Runtime if it is missing.

#define AppName "OptiCIP Dashboard"
; Версия приходит из единого источника (webapp/__init__.py: __version__) —
; её передают build_windows.bat и CI: ISCC.exe /DAppVersion=1.2.3 installer.iss.
; Значение ниже — только запасное, для ручного вызова ISCC без параметров.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppPublisher "OptiCIP"
#define AppExe "OptiCIP-Dashboard.exe"
; Тот же мьютекс создаёт приложение (run_wash_desktop.py, acquire_single_instance_lock).
#define AppSingleInstanceMutex "Local\OptiCIP-Dashboard-SingleInstance"

[Setup]
AppId={{B7E6F1A2-3C4D-4E5F-9A1B-2C3D4E5F6A7B}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=installer_out
OutputBaseFilename=OptiCIP-Dashboard-Setup
SetupIconFile=webapp\static\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
; Обновление поверх работающего приложения иначе упирается в занятый .exe:
; просим закрыть его до копирования файлов.
AppMutex={#AppSingleInstanceMutex}

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
; WebView2 evergreen bootstrapper (downloaded by CI before compiling the installer).
Source: "MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "Установка среды Microsoft Edge WebView2..."; Check: not WebView2Installed; Flags: waituntilterminated
; runasoriginaluser обязателен: установщик работает с админским токеном, и без
; этого флага приложение стартовало бы под администратором — его данные, ключ
; DPAPI (пароль FTP) и автозапуск HKCU достались бы админу, а не оператору.
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent runasoriginaluser
; Автообновление из приложения: оно запускает установщик с /SILENT /RELAUNCH=1 и
; закрывается. Строка выше при /SILENT пропускается (skipifsilent), поэтому
; поднимаем приложение обратно здесь — иначе после обновления окно бы не
; вернулось и выглядело бы как вылет. runasoriginaluser по той же причине, что
; и выше: под админом приложение писало бы данные и DPAPI-ключ не тому юзеру.
Filename: "{app}\{#AppExe}"; Flags: nowait runasoriginaluser; Check: WantsRelaunch

[UninstallRun]
; Автозапуск приложение пишет в HKCU\...\Run текущего пользователя; при удалении
; просим само приложение снять запись (--remove-autostart).
; Флаг runasoriginaluser в [UninstallRun] Inno Setup НЕ поддерживается (доступен
; только в [Run]) — из-за него компилятор падал с «flag not supported in this
; section». Поэтому очистка идёт в обычном контексте деинсталлятора: она
; срабатывает, когда удаление запускает тот же пользователь, что ставил программу
; (обычный случай). В редком случае удаления под другой учётной записью запись
; автозапуска останется указывать на удалённый .exe — Windows молча игнорирует
; такой «мёртвый» автозапуск, вреда нет.
Filename: "{app}\{#AppExe}"; Parameters: "--remove-autostart"; Flags: waituntilterminated runhidden skipifdoesntexist; RunOnceId: "RemoveAutostart"

[Code]
// Сброс кэша иконок оболочки. Путь установки и AppId у нас постоянные, поэтому
// при обновлении поверх старой версии Проводник продолжает рисовать иконку,
// запомненную при первой установке: ярлыки ссылаются на {app}\AppExe без
// IconFilename, а кэш ключуется по пути и обновляется неохотно. Так у всех, кто
// обновляется с 1.0.x, на рабочем столе оставалась прежняя иконка-капля, хотя в
// .exe уже лежит новая. SHCNE_ASSOCCHANGED — штатный способ заставить оболочку
// сбросить кэш иконок; файлы iconcache*.db при этом не трогаются и Проводник не
// перезапускается.
const
  SHCNE_ASSOCCHANGED = $08000000;
  SHCNF_IDLIST = $00000000;

procedure SHChangeNotify(wEventId: Integer; uFlags: Cardinal; dwItem1: Cardinal; dwItem2: Cardinal);
  external 'SHChangeNotify@shell32.dll stdcall';

procedure CurStepChanged(CurStep: TSetupStep);
begin
  // ssPostInstall — файлы уже скопированы и ярлыки созданы, но приложение из
  // [Run] ещё не стартовало: оболочка перечитает иконку из нового .exe.
  if CurStep = ssPostInstall then
    SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, 0, 0);
end;

// /RELAUNCH=1 передаёт только автообновление из приложения (см. [Run]).
// При обычной ручной установке параметра нет — поведение мастера не меняется.
function WantsRelaunch(): Boolean;
begin
  Result := ExpandConstant('{param:RELAUNCH|0}') = '1';
end;

function WebView2Installed(): Boolean;
var
  Value: String;
begin
  Result :=
    (RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Value) and (Value <> '') and (Value <> '0.0.0.0'))
    or (RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Value) and (Value <> '') and (Value <> '0.0.0.0'))
    or (RegQueryStringValue(HKCU, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Value) and (Value <> '') and (Value <> '0.0.0.0'));
end;
