// Десктоп-режим (pywebview): выбор папок через нативный диалог, кастомная
// шапка окна и состояние «развёрнуто». В браузере всё это тихо выключается.

import { showToast } from "./toast.js";

function hasDesktopFolderPickerApi() {
  return typeof window.pywebview?.api?.choose_folder === "function";
}

function submitForm(form) {
  if (!form) {
    return;
  }

  form.requestSubmit();
}

async function requestFolderPath(initialPath = "") {
  if (!hasDesktopFolderPickerApi()) {
    throw new Error("desktop-folder-picker-unavailable");
  }

  const response = await window.pywebview.api.choose_folder({
    initial_path: initialPath,
  });

  if (response?.cancelled) {
    return null;
  }

  if (!response?.ok || !response?.path) {
    throw new Error("desktop-folder-picker-failed");
  }

  return response.path;
}

export function initFolderPickerButtons() {
  const folderPickerButtons = Array.from(document.querySelectorAll("[data-folder-picker]"));
  if (!folderPickerButtons.length) {
    return;
  }

  folderPickerButtons.forEach((button) => {
    button.addEventListener("click", async () => {
      const targetInputId = button.dataset.targetInput;
      const submitFormId = button.dataset.submitForm;
      const targetInput = targetInputId ? document.getElementById(targetInputId) : null;
      const targetForm = submitFormId ? document.getElementById(submitFormId) : targetInput?.form || null;
      const originalLabel = button.textContent?.trim() || "Выбрать папку";

      button.disabled = true;
      button.textContent = "Выбираю папку...";

      try {
        const selectedPath = await requestFolderPath(targetInput?.value || "");
        if (!selectedPath) {
          return;
        }

        if (targetInput) {
          targetInput.value = selectedPath;
        }

        if (targetForm) {
          submitForm(targetForm);
        }
      } catch (_error) {
        showToast("Не удалось открыть выбор папки. Попробуйте ещё раз.", "error");
      } finally {
        button.disabled = false;
        button.textContent = originalLabel;
      }
    });
  });
}

export function initFolderDefaultButtons() {
  const folderDefaultButtons = Array.from(document.querySelectorAll("[data-folder-default]"));
  if (!folderDefaultButtons.length) {
    return;
  }

  folderDefaultButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const targetInputId = button.dataset.targetInput;
      const targetInput = targetInputId ? document.getElementById(targetInputId) : null;
      const defaultPath = button.dataset.defaultPath || "";
      if (!targetInput || !defaultPath) {
        return;
      }
      targetInput.value = defaultPath;
      targetInput.focus();
    });
  });
}

// Синхронизирует класс window-maximized с РЕАЛЬНЫМ состоянием окна (на случай
// разворачивания средствами ОС — Aero Snap, Win+↑, перетаскивание к верху).
export async function refreshWindowMaximizedState() {
  const api = window.pywebview && window.pywebview.api;
  if (!api || typeof api.get_window_state !== "function") {
    return;
  }
  try {
    const state = await api.get_window_state();
    if (state && typeof state.maximized === "boolean") {
      document.body.classList.toggle("window-maximized", state.maximized);
    }
  } catch (_error) {
    // Оставляем текущее состояние класса.
  }
}

// Кастомная шапка окна (frameless desktop-режим). В обычном браузере
// window.pywebview отсутствует — шапка остаётся скрытой, лейаут не меняется.
export function initDesktopTitlebar() {
  const titlebar = document.querySelector("[data-app-titlebar]");
  if (!titlebar) {
    return;
  }

  const callWindowApi = (method) => {
    const api = window.pywebview && window.pywebview.api;
    if (api && typeof api[method] === "function") {
      Promise.resolve(api[method]()).catch(() => {});
    }
  };

  // Признак развёрнутого окна на body — по нему в уменьшённом режиме модалка
  // графика показывает только сам график (без сводки и параметров кривых).
  const setMaximizedState = (maximized) => {
    document.body.classList.toggle("window-maximized", Boolean(maximized));
  };

  const toggleMaximize = () => {
    const api = window.pywebview && window.pywebview.api;
    if (api && typeof api.toggle_maximize === "function") {
      Promise.resolve(api.toggle_maximize())
        .then((result) => {
          if (result && typeof result.maximized === "boolean") {
            setMaximizedState(result.maximized);
          }
        })
        .catch(() => {});
    }
  };

  titlebar.querySelector("[data-window-min]")?.addEventListener("click", () => callWindowApi("minimize_window"));
  titlebar.querySelector("[data-window-max]")?.addEventListener("click", toggleMaximize);
  titlebar.querySelector("[data-window-close]")?.addEventListener("click", () => callWindowApi("close_window"));
  titlebar.querySelector("[data-titlebar-drag]")?.addEventListener("dblclick", toggleMaximize);

  const enableDesktopShell = () => {
    document.body.classList.add("desktop-shell");
    titlebar.hidden = false;
    // Приложение открывается в уменьшённом окне (не развёрнуто).
    setMaximizedState(false);
  };

  if (window.pywebview && window.pywebview.api) {
    enableDesktopShell();
  } else {
    // pywebview внедряет мост чуть позже загрузки страницы.
    window.addEventListener("pywebviewready", enableDesktopShell, { once: true });
  }
}
