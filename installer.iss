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
; Установка в профиль пользователя ({localappdata}\Programs через {autopf}) —
; именно ради этого стоит lowest: установка в Program Files требует прав
; администратора, а значит Windows спрашивала бы разрешение при КАЖДОМ
; обновлении. Приложению админские права не нужны: все свои данные
; (настройки, datalog, кэши, ключ DPAPI) оно и раньше держало в
; %LOCALAPPDATA%\OptiCIP Dashboard, а автозапуск писало в HKCU — смена каталога
; установки ничего из этого не теряет.
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
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "Установка среды Microsoft Edge WebView2..."; Check: not WebView2Installed; Flags: waituntilterminated
; Сброс кэша иконок оболочки — половина «очисти» (половина «перерисуй» — это
; SHChangeNotify в [Code], см. комментарий там). Без него обновившиеся с 1.0.x
; продолжают видеть старую иконку в «Пуске» и на рабочем столе.
;
; ie4uinit — родная утилита оболочки: перестраивает кэш иконок, НЕ перезапуская
; Проводник и не удаляя iconcache_*.db руками. Ручной рецепт (taskkill explorer +
; del iconcache*.db) работает, но закрывает пользователю все окна папок — при
; тихом автообновлении это выглядело бы как сбой, поэтому здесь он не годится.
;
; runasoriginaluser с PrivilegesRequired=lowest — уже не обязателен (установщик
; и так работает от имени пользователя, флаг просто ничего не делает), но
; оставлен намеренно: он снова станет значимым, если установку когда-нибудь
; вернут под администратора. Кэш иконок лежит в профиле пользователя
; (%LocalAppData%\Microsoft\Windows\Explorer), и из-под админского токена мы
; перестроили бы чужой кэш.
;
; skipifdoesntexist — подстраховка: ie4uinit есть во всех поддерживаемых
; Windows, но отсутствие косметической утилиты не повод ронять установку.
Filename: "{sys}\ie4uinit.exe"; Parameters: "-show"; Flags: runasoriginaluser runhidden skipifdoesntexist
; runasoriginaluser: под lowest — no-op (установщик уже работает от пользователя),
; оставлен на случай возврата к админской установке. Тогда без него приложение
; стартовало бы под администратором, и его данные, ключ DPAPI (пароль FTP) и
; автозапуск HKCU достались бы админу, а не оператору.
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
  // Ключ деинсталляции прежней (админской) установки в Program Files — см.
  // PrepareToInstall ниже.
  LegacyUninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{B7E6F1A2-3C4D-4E5F-9A1B-2C3D4E5F6A7B}_is1';

procedure SHChangeNotify(wEventId: Integer; uFlags: Cardinal; dwItem1: Cardinal; dwItem2: Cardinal);
  external 'SHChangeNotify@shell32.dll stdcall';

procedure CurStepChanged(CurStep: TSetupStep);
begin
  // Именно ssDone, а не ssPostInstall: секция [Run] выполняется между ними, а
  // порядок здесь важен — сначала ie4uinit чистит кэш, и только потом имеет
  // смысл просить оболочку перерисовать. На ssPostInstall уведомление ушло бы
  // до очистки и пропало впустую.
  if CurStep = ssDone then
    SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, 0, 0);
end;

// ---- миграция с прежней админской установки --------------------------------
// До 1.1.31 приложение ставилось в Program Files с правами администратора.
// Ключ деинсталляции той установки лежит в HKLM, а наш per-user установщик
// смотрит в HKCU — сам он старую копию не увидит и просто поставит вторую.
// Это плохо: в «Программах и компонентах» остаётся мёртвая запись, в «Пуске» —
// два ярлыка, а автозапуск (его пишет приложение в HKCU) мог бы указывать на
// старый .exe, и после перезагрузки поднималась бы прежняя версия.
//
// Поэтому перед установкой пробуем тихо снести старую копию. Это единственное
// место, где Windows ещё раз спросит разрешение — удаление из Program Files
// без прав администратора невозможно. Одно разрешение один раз, дальше
// обновления идут молча.
//
// Отказ пользователя не считается ошибкой: ставим новую копию рядом, старая
// останется лежать. Ронять установку из-за этого нельзя — обновление должно
// доехать в любом случае.
function ReadLegacyUninstaller(): String;
var
  Value: String;
begin
  Result := '';
  if RegQueryStringValue(HKLM64, LegacyUninstallKey, 'UninstallString', Value) and (Value <> '') then
    Result := Value
  else if RegQueryStringValue(HKLM32, LegacyUninstallKey, 'UninstallString', Value) and (Value <> '') then
    Result := Value;
  // UninstallString приходит в кавычках — ShellExec ждёт чистый путь.
  Result := RemoveQuotes(Result);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Uninstaller: String;
  ResultCode: Integer;
  Waited: Integer;
begin
  Result := '';
  Uninstaller := ReadLegacyUninstaller();
  if (Uninstaller = '') or (not FileExists(Uninstaller)) then
    exit;

  Log('Найдена прежняя установка в Program Files: ' + Uninstaller);
  // 'runas' — деинсталлятору нужны права администратора; это и есть тот самый
  // единственный запрос UAC при переходе на per-user установку.
  if not ShellExec('runas', Uninstaller, '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART', '',
                   SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    Log('Удалить прежнюю установку не удалось (отказ UAC или ошибка запуска) — ставим рядом.');
    exit;
  end;

  // Деинсталлятор Inno копирует себя во временный каталог и завершает исходный
  // процесс сразу, поэтому ewWaitUntilTerminated возвращается раньше, чем файлы
  // действительно удалены. Ждём исчезновения ключа, но недолго: наша установка
  // не должна зависнуть из-за чужого деинсталлятора.
  Waited := 0;
  while (Waited < 30000) and (ReadLegacyUninstaller() <> '') do
  begin
    Sleep(500);
    Waited := Waited + 500;
  end;
  Log('Прежняя установка удалена: ' + IntToStr(ResultCode));
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
