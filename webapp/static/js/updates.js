// Автообновление: скачивание и запуск установщика, проверка при старте и кнопка
// обновления на стартовом экране. Панель обновлений в настройках пока в main.js.

import { apiJson } from "./core/api.js";

// Скачать установщик и запустить установку — общее для автообновления при
// старте, кнопки стартового экрана и панели в настройках. Самодостаточно (без
// state): стартовый экран зовёт его до гейта. onJob получает каждый статус
// скачивания; isCancelled() → true прекращает опрос (скачивание на сервере
// продолжится и подхватится повторным запуском). Потолок опроса ≈20 минут
// (500 мс × 2400): залипший в "running" бэкенд не опрашиваем вечно.
export async function downloadAndInstallUpdate({ onJob = () => {}, isCancelled = () => false } = {}) {
  const started = await apiJson("/api/update/download", {
    method: "POST",
    timeout: 20000,
    errorMessage: "Не удалось начать скачивание.",
  });
  let job = started?.job || null;
  if (job) {
    onJob(job);
  }
  for (let ticks = 0; ; ticks += 1) {
    if (ticks >= 2400) {
      throw new Error("Скачивание не завершилось за отведённое время.");
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
    if (isCancelled()) {
      return false;
    }
    job = await apiJson("/api/update/job", {
      timeout: 15000,
      errorMessage: "Не удалось получить статус скачивания.",
    });
    onJob(job);
    if (!job || job.status !== "running") {
      break;
    }
  }
  if (!job || job.status !== "ready") {
    throw new Error(job?.error || "Не удалось скачать обновление.");
  }
  // Отказ от UAC приходит сюда же: приложение остаётся работать.
  const result = await window.pywebview.api.install_update();
  if (!result?.ok) {
    throw new Error(result?.error || "Не удалось запустить установщик.");
  }
  return true;
}

// ---- Автообновление при запуске ----------------------------------------
// Полный цикл без единого нажатия: проверка → скачивание → тихая установка →
// перезапуск. Самодостаточно (как кнопка на экране выбора источника): код
// работает ДО гейта `if (!washList) return`, где state/showToast ещё в TDZ,
// поэтому только fetch и собственный баннер.
//
// Почему только при старте, а не по таймеру: установка закрывает приложение,
// и прервать оператора посреди смены — хуже, чем обновиться минутой позже.
// Один запрос UAC остаётся: установщик пишет в Program Files. Убрать его
// можно лишь per-user установкой (P0-3 в ROADMAP).
export function initAutoUpdate() {
  const showBanner = (text) => {
    let banner = document.querySelector("[data-auto-update-banner]");
    if (!banner) {
      banner = document.createElement("div");
      banner.className = "auto-update-banner";
      banner.setAttribute("data-auto-update-banner", "");
      banner.setAttribute("role", "status");
      document.body.append(banner);
    }
    banner.textContent = text;
  };

  const hideBanner = () => {
    document.querySelector("[data-auto-update-banner]")?.remove();
  };

  async function run() {
    // Мост pywebview ставит установщик и закрывает окно; в браузере автообновление
    // невозможно в принципе.
    if (typeof window.pywebview?.api?.install_update !== "function") {
      return;
    }

    try {
      const payload = await apiJson("/api/settings", { timeout: 15000 });
      const settings = payload && typeof payload.settings === "object" ? payload.settings : {};
      if (settings.auto_update_enabled === false) {
        return;
      }

      const info = await apiJson("/api/update-check", { timeout: 15000 });
      if (!info || !info.update_available || !info.installable) {
        return;
      }

      showBanner(`Обновление ${info.latest}: скачиваю…`);
      await downloadAndInstallUpdate({
        onJob: (job) => {
          if (job.status !== "running") {
            showBanner(`Обновление ${info.latest}: устанавливаю, приложение перезапустится…`);
          } else if (job.total > 0) {
            const pct = Math.min(100, Math.round((job.downloaded / job.total) * 100));
            showBanner(`Обновление ${info.latest}: скачиваю… ${pct}%`);
          }
        },
      });
    } catch (_error) {
      // Сюда же приходит отказ от UAC: приложение остаётся работать, и это
      // нормальный исход — молча убираем баннер, не пугая оператора ошибкой.
      hideBanner();
    }
  }

  if (window.pywebview && window.pywebview.api) {
    run();
  } else {
    window.addEventListener("pywebviewready", run, { once: true });
  }
}

// Умная кнопка обновления на экране выбора источника. САМОДОСТАТОЧНА: экран
// рендерится ДО гейта `if (!hasWorkspace) return`, где state/
// checkForUpdates ещё в TDZ — только fetch + свои элементы. Режим «check» —
// проверка; если есть устанавливаемое обновление, та же кнопка становится
// «Установить обновление» (режим «install»).
export function initWelcomeUpdateButton() {
  const button = document.querySelector("[data-update-btn]");
  if (!button) {
    return;
  }
  const statusEl = document.querySelector("[data-update-status]");
  const setStatus = (text) => {
    if (statusEl) {
      statusEl.textContent = text || "";
    }
  };
  const CHECK_LABEL = "Проверить обновления";
  const INSTALL_LABEL = "Установить обновление";
  let mode = "check";
  let busy = false;

  // Установить «в один клик» можно только собранную Windows-версию через мост
  // pywebview. Иначе (браузер/не-Windows) — только ссылка на GitHub Releases.
  const canInstall = (data) =>
    !!data &&
    data.update_available &&
    data.installable &&
    typeof window.pywebview?.api?.install_update === "function";

  async function runCheck() {
    button.textContent = "Проверяю…";
    setStatus("");
    try {
      const data = await apiJson("/api/update-check", { timeout: 15000 });
      if (data.update_available && canInstall(data)) {
        mode = "install"; // та же кнопка превращается в «Установить обновление»
        setStatus(`Доступно обновление ${data.latest}.`);
        button.textContent = INSTALL_LABEL;
        return;
      }
      if (data.update_available) {
        setStatus(`Доступно обновление ${data.latest}. Смотрите GitHub Releases.`);
      } else if (data.latest) {
        setStatus("Установлена последняя версия.");
      } else {
        // Пустой latest — «не выяснили» (нет сети/релизов), не «актуально».
        setStatus("Не удалось проверить обновления.");
      }
      button.textContent = CHECK_LABEL;
    } catch (_error) {
      setStatus("Не удалось проверить обновления.");
      button.textContent = CHECK_LABEL;
    }
  }

  async function runInstall() {
    setStatus("Скачиваю обновление…");
    try {
      await downloadAndInstallUpdate({
        onJob: (job) => {
          if (job.status === "running" && job.total > 0) {
            const pct = Math.min(100, Math.round((job.downloaded / job.total) * 100));
            setStatus(`Скачиваю обновление… ${pct}%`);
          }
        },
      });
      setStatus("Запускаю установку — приложение закроется…");
      // Успех: приложение закроется — кнопку в исходное не возвращаем.
    } catch (error) {
      setStatus(String(error.message || error));
      button.textContent = INSTALL_LABEL; // остаёмся в режиме установки (повтор)
    }
  }

  button.addEventListener("click", async () => {
    if (busy) {
      return;
    }
    busy = true;
    button.disabled = true;
    try {
      if (mode === "install") {
        await runInstall();
      } else {
        await runCheck();
      }
    } finally {
      busy = false;
      button.disabled = false;
    }
  });
}
