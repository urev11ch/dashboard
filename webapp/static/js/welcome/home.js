// Главный экран: режим CIP | ПОУ и карточки источников (FTP-панели и папки).
// Режим — фильтр карточек: атрибут data-home-mode на <html> (ставится скриптом
// в <head> до отрисовки) + выбор в localStorage. Каждый источник открывается в
// своём типе; режим лишь показывает источники одного типа.

import { openFormDialog } from "../core/dialogs.js";
import { applyWashResultVisibility } from "../workspace/result-visibility.js";

const HOME_MODE_KEY = "opticipHomeModeV1";
const HOME_MODES = ["cip", "pou"];

export function currentHomeMode() {
  const mode = document.documentElement.dataset.homeMode;
  return HOME_MODES.includes(mode) ? mode : "cip";
}

function setHomeMode(mode) {
  const next = HOME_MODES.includes(mode) ? mode : "cip";
  document.documentElement.dataset.homeMode = next;
  try {
    localStorage.setItem(HOME_MODE_KEY, next);
  } catch (_error) {
    /* localStorage недоступен — режим действует до перезагрузки */
  }
  document.querySelectorAll("[data-home-mode]").forEach((button) => {
    button.setAttribute("aria-selected", button.dataset.homeMode === next ? "true" : "false");
  });
}

export function initHome() {
  const home = document.querySelector("[data-home]");
  if (!home) {
    return;
  }

  setHomeMode(currentHomeMode());
  home.querySelectorAll("[data-home-mode]").forEach((button) => {
    button.addEventListener("click", () => setHomeMode(button.dataset.homeMode));
  });

  // Вердикт мойки на карточке скрыт, пока скрыта колонка «Результат» в журнале.
  applyWashResultVisibility();

  // Открытие источника: запоминаем его режим (возврат в меню покажет тот же
  // режим); у папки — сбрасываем контекст панели для кнопки WebView журнала.
  document.querySelectorAll("form[data-home-open]").forEach((form) => {
    form.addEventListener("submit", () => {
      setHomeMode(form.dataset.equipmentTarget);
      if (!form.hasAttribute("data-panel-graphs")) {
        try {
          sessionStorage.removeItem("opticip.panel");
        } catch (_error) {
          /* sessionStorage недоступен — не критично */
        }
      }
    });
  });

  // Двойной клик по карточке — «Открыть журнал». Клики по кнопкам и меню
  // карточки сюда не относятся.
  home.querySelectorAll("[data-source-card]").forEach((card) => {
    card.addEventListener("dblclick", (event) => {
      if (event.target.closest("button, a, input, summary, details, form")) {
        return;
      }
      card.querySelector("form[data-home-open]")?.requestSubmit();
    });
  });

  home.querySelectorAll("[data-source-rename]").forEach((button) => {
    button.addEventListener("click", () => {
      button.closest("details")?.removeAttribute("open");
      openFormDialog({
        action: "/workspace/source/rename",
        title: "Название источника",
        hidden: [
          ["source_kind", button.dataset.kind || ""],
          ["source_id", button.dataset.id || ""],
        ],
        fields: [
          {
            label: "Название",
            name: "label",
            value: button.dataset.label || "",
            required: true,
            focus: true,
            select: true,
          },
        ],
        submitLabel: "Сохранить",
      });
    });
  });

  home.querySelectorAll("form[data-source-delete]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm || "Удалить источник?")) {
        event.preventDefault();
      }
    });
  });

  // Добавляемый источник получает текущий режим (для папки — запасной тип,
  // если по содержимому его не определить).
  document.querySelectorAll("form").forEach((form) => {
    const input = form.querySelector("[data-equipment-input]");
    if (input) {
      form.addEventListener("submit", () => {
        input.value = currentHomeMode();
      });
    }
  });

  const folderToggle = home.querySelector("[data-folder-add-toggle]");
  const folderPanel = document.querySelector("[data-folder-add]");
  if (folderToggle && folderPanel) {
    folderToggle.addEventListener("click", () => {
      const willOpen = folderPanel.hidden;
      folderPanel.hidden = !willOpen;
      folderToggle.setAttribute("aria-expanded", willOpen ? "true" : "false");
      if (willOpen) {
        folderPanel.querySelector('[name="path"]')?.focus();
      }
    });
  }
}
