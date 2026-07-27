; Inno Setup script for OptiCIP Dashboard.
; Builds installer_out\OptiCIP-Dashboard-Setup.exe from dist\OptiCIP-Dashboard.exe.
; Installs the app, creates shortcuts and silently installs the Microsoft Edge
; WebView2 Runtime if it is missing.

#define AppName "OptiCIP Dashboard"
; Версия приходит из единого источника (webapp/__init__.py: __version__) —
; её передают build_windows.bat и CI: ISCC.exe /DAppVersion=1.2.3 installer.iss.
; Фолбэка здесь намеренно нет: он собирался бы молча и давал установщик, который
; представляется системе не своей версией — а по ней идут «Программы и компоненты»
; и обновление поверх. Лучше упасть на компиляции, чем выпустить такой релиз.
#ifndef AppVersion
  #error AppVersion не задан. Запускайте build_windows.bat либо передайте версию явно: ISCC.exe /DAppVersion=1.2.3 installer.iss (номер — из webapp/__init__.py, __version__).
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
; Per-user установка (без UAC): PrivilegesRequired=lowest → установщик работает от
; имени пользователя, а константы {autopf}/{autoprograms}/{autodesktop}
; автоматически превращаются в пользовательские (%LocalAppData%\Programs,
; пользовательские «Пуск»/рабочий стол). Это даёт ТИХОЕ фоновое обновление без
; запроса UAC (см. run_wash_desktop.run_auto_update_loop). Данные (datalog/temp,
; ключ DPAPI, автозапуск HKCU) и так per-user — при переезде не теряются.
; PrivilegesRequiredOverridesAllowed пусто: не даём поднимать до admin.
PrivilegesRequired=lowest
; Обновление поверх работающего приложения иначе упирается в занятый .exe:
; просим закрыть его до копирования файлов.
AppMutex={#AppSingleInstanceMutex}

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; Без unchecked (было до 1.1.8): дашборд запускает оператор, ярлык на столе ему
; нужен по умолчанию. Побочный и важный эффект — ярлык пересоздаётся при каждой
; установке, включая тихое автообновление. Пока задача была unchecked, ярлык в
; «Пуске» обновлялся всегда, а десктопный не трогался никогда — и именно на нём
; дольше всего жила старая иконка. Теперь это второй рубеж на случай, если
; сброс кэша иконок (ie4uinit в [Run]) где-то не сработает.
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
; WebView2 evergreen bootstrapper (downloaded by CI before compiling the installer).
Source: "MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; При PrivilegesRequired=lowest установщик работает от имени пользователя, поэтому
; флаг runasoriginaluser больше не нужен (он имел смысл только под админ-токеном).
; WebView2 при non-elevated ставится per-user; проверка WebView2Installed смотрит
; и HKCU, так что уже установленный (в т.ч. системный) рантайм пропускается.
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "Установка среды Microsoft Edge WebView2..."; Check: not WebView2Installed; Flags: waituntilterminated
; Сброс кэша иконок оболочки — половина «очисти» (половина «перерисуй» — это
; SHChangeNotify в [Code], см. комментарий там). Без него обновившиеся с 1.0.x
; продолжают видеть старую иконку в «Пуске» и на рабочем столе. ie4uinit —
; родная утилита оболочки: перестраивает кэш иконок, НЕ перезапуская Проводник.
; skipifdoesntexist — подстраховка: отсутствие косметической утилиты не повод
; ронять установку.
Filename: "{sys}\ie4uinit.exe"; Parameters: "-show"; Flags: runhidden skipifdoesntexist
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
; Автообновление из приложения: оно запускает установщик с /SILENT /RELAUNCH=1 и
; закрывается. Строка выше при /SILENT пропускается (skipifsilent), поэтому
; поднимаем приложение обратно здесь — иначе после обновления окно бы не
; вернулось и выглядело бы как вылет.
Filename: "{app}\{#AppExe}"; Flags: nowait; Check: WantsRelaunch

[UninstallRun]
; Автозапуск приложение пишет в HKCU\...\Run текущего пользователя; при удалении
; просим само приложение снять запись (--remove-autostart). Деинсталлятор при
; per-user установке и так работает от имени пользователя, поэтому запись HKCU\Run
; чистится в его контексте штатно.
Filename: "{app}\{#AppExe}"; Parameters: "--remove-autostart"; Flags: waituntilterminated runhidden skipifdoesntexist; RunOnceId: "RemoveAutostart"

[Code]
// Половина «перерисуй» в сбросе кэша иконок: сообщаем оболочке, что иконки
// изменились. Путь установки и AppId у нас постоянные, поэтому при обновлении
// поверх старой версии Проводник продолжает рисовать иконку, запомненную при
// первой установке: ярлыки ссылаются на {app}\AppExe без IconFilename, а кэш
// ключуется по пути целевого .exe. У обновившихся с 1.0.x из-за этого и в меню
// «Пуск», и на рабочем столе оставалась прежняя иконка-капля, хотя в .exe с
// 1.1.0 лежит новая (в панели задач при этом видна новая: иконку окна процесс
// грузит из .exe напрямую, мимо кэша оболочки).
//
// ВАЖНО: одного этого вызова недостаточно — проверено на живой машине в 1.1.7.
// На Windows 10/11 SHCNE_ASSOCCHANGED не вычищает iconcache_*.db для иконок,
// уже закэшированных по пути. Половину «очисти» делает ie4uinit в [Run]; здесь
// остаётся только уведомление оболочки о перерисовке.
const
  SHCNE_ASSOCCHANGED = $08000000;
  SHCNF_IDLIST = $00000000;

procedure SHChangeNotify(wEventId: Integer; uFlags: Cardinal; dwItem1: Cardinal; dwItem2: Cardinal);
  external 'SHChangeNotify@shell32.dll stdcall';

// ВОЗМОЖНОСТЬ ОТКАТА (переходный релиз Program Files → per-user):
// прежнюю admin-копию в Program Files НЕ трогаем — ни при тихой, ни при ручной
// установке. Она остаётся рабочей точкой отката: если новая per-user версия
// повела себя не так, оператор запускает старую копию (её ярлык в общем «Пуске»)
// или переустанавливает предыдущий релиз 1.1.23 (он остаётся на GitHub Releases).
// Плата — временно два одноимённых ярлыка «Пуска» (общий → Program Files и
// пользовательский → %LocalAppData%). Когда переход подтверждён, старую копию
// можно снять вручную («Программы и компоненты»).
procedure CurStepChanged(CurStep: TSetupStep);
begin
  // Именно ssDone, а не ssPostInstall: секция [Run] выполняется между ними, а
  // порядок здесь важен — сначала ie4uinit чистит кэш, и только потом имеет
  // смысл просить оболочку перерисовать. На ssPostInstall уведомление ушло бы
  // до очистки и пропало впустую.
  if CurStep = ssDone then
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
