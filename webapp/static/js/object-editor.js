// Редактор названий: имена объектов (канал + номер) и программ мойки.

import { apiJson } from "./core/api.js";
import { syncOverlayState } from "./core/overlays.js";
import { showToast } from "./core/toast.js";
import {
  channelLabelFor,
  channelsFromRows,
  normalizeObjectName,
  replaceObjectRows,
  state,
} from "./workspace/store.js";
import { applyObjectNameToWashData } from "./workspace/wash-list.js";

const { escapeHtml } = window.WashChart;

// Зависимости от main.js: корневой элемент окна (порядок окон в DOM задаёт
// наложение), beforeOpen (закрыть окно мойки) и hydrateWorkspaceData (загрузка
// данных живёт в main.js). Задаются один раз в initObjectEditor.
let objectEditorRoot = null;
let beforeOpen = () => {};
let hydrateWorkspaceData = null;

export function initObjectEditor(root, deps) {
  objectEditorRoot = root;
  ({
    beforeOpen,
    hydrateWorkspaceData,
  } = deps);
}

function renderObjectEditorChannelChoices(selectedValue = 1) {
  const selectedChannel = Number(selectedValue || 1);
  // Потоки берём из найденных данных: имена файлов на панелях произвольные,
  // фиксированной сетки «каналы 1..5» больше нет.
  const channels = channelsFromRows(state.objectRows);
  if (!channels.length) {
    channels.push(selectedChannel >= 1 ? selectedChannel : 1);
  }
  return channels
    .map(
      (channel) => `
        <button
          type="button"
          class="object-editor-choice${channel === selectedChannel ? " is-selected" : ""}"
          data-choice-group="channel"
          data-choice-value="${channel}"
          aria-pressed="${channel === selectedChannel ? "true" : "false"}"
        >
          ${escapeHtml(channelLabelFor(channel))}
        </button>
      `
    )
    .join("");
}

function renderObjectEditorIdChoices(selectedValue = 1) {
  const selectedId = Number(selectedValue || 1);
  return Array.from({ length: 30 }, (_, index) => index + 1)
    .map(
      (objectId) => `
        <button
          type="button"
          class="object-editor-choice object-editor-choice--id${objectId === selectedId ? " is-selected" : ""}"
          data-choice-group="object_id"
          data-choice-value="${objectId}"
          aria-pressed="${objectId === selectedId ? "true" : "false"}"
        >
          ${objectId}
        </button>
      `
    )
    .join("");
}

function syncObjectEditorChoiceGroup(root, groupName, rawValue) {
  if (!root) {
    return;
  }

  const normalizedValue = String(rawValue);
  root.querySelectorAll(`[data-choice-group="${groupName}"]`).forEach((button) => {
    const isSelected = String(button.dataset.choiceValue || "") === normalizedValue;
    button.classList.toggle("is-selected", isSelected);
    button.setAttribute("aria-pressed", isSelected ? "true" : "false");
  });
}

function findObjectEditorRow(channel, objectId) {
  return (
    state.objectRows.find(
      (row) =>
        Number(row.channel) === Number(channel) && Number(row.object_id) === Number(objectId)
    ) || null
  );
}

function findDuplicateObjectName(channel, objectId, objectName) {
  const normalizedName = normalizeObjectName(objectName).toLowerCase();
  if (!normalizedName) {
    return null;
  }

  return (
    state.objectRows.find(
      (row) =>
        !(
          Number(row.channel) === Number(channel) && Number(row.object_id) === Number(objectId)
        ) && normalizeObjectName(row.object_name).toLowerCase() === normalizedName
    ) || null
  );
}

function findFirstAvailableObjectSlot() {
  for (let channel = 1; channel <= 5; channel += 1) {
    for (let objectId = 1; objectId <= 30; objectId += 1) {
      if (!findObjectEditorRow(channel, objectId)) {
        return { channel, objectId };
      }
    }
  }

  return { channel: 1, objectId: 1 };
}

function persistObjectName(channel, objectId, name = "", mode = "set") {
  return apiJson("/api/object-name", {
    method: "POST",
    body: { channel, object_id: objectId, name, mode },
    errorMessage: "Не удалось сохранить новое название объекта.",
  });
}

export function closeObjectEditor() {
  objectEditorRoot.hidden = true;
  objectEditorRoot.innerHTML = "";
  syncOverlayState();
}

function renderObjectEditorRows() {
  if (!state.objectRows.length) {
    return '<div class="technical-empty">Объекты не найдены.</div>';
  }

  return state.objectRows
    .map(
      (row) => `
        <form class="object-editor-row" data-object-editor-form>
          <input type="hidden" name="channel" value="${escapeHtml(row.channel)}">
          <input type="hidden" name="object_id" value="${escapeHtml(row.object_id)}">
          <div class="object-editor-row-meta">
            <div class="object-editor-row-identity">
              <span class="object-editor-token">${escapeHtml(
                row.channel_label || channelLabelFor(row.channel)
              )}</span>
              <span class="object-editor-token">Объект ${escapeHtml(row.object_id)}</span>
            </div>
          </div>
          <div class="object-editor-row-controls">
            <input
              class="object-editor-name-input"
              type="text"
              name="object_name"
              value="${escapeHtml(row.object_name)}"
              placeholder="Название объекта"
              autocomplete="off"
              spellcheck="false"
            >
          </div>
          <div class="object-editor-row-actions">
            <button type="submit" class="object-editor-row-button object-editor-row-button--primary">Сохранить</button>
            <button
              type="button"
              class="ghost object-editor-row-button"
              data-object-editor-reset
              data-channel="${escapeHtml(row.channel)}"
              data-object-id="${escapeHtml(row.object_id)}"
              data-object-name="${escapeHtml(row.object_name)}"
            >
              Сбросить
            </button>
          </div>
        </form>
      `
    )
    .join("");
}

// ---- редактор названий программ мойки -------------------------------
// Названия программ в архиве панели не хранятся — там только номер. Что этот
// номер значит, знает лишь пользователь, поэтому названия задаются здесь и
// ложатся в wash_program_names.json. Список программ на станции один и тот же
// для всех объектов, поэтому название одно на номер.
export function replaceProgramRows(rows) {
  state.programRows = Array.isArray(rows) ? rows.map((row) => ({ ...row })) : [];
}

function persistProgramName(programId, name = "", mode = "set") {
  return apiJson("/api/program-name", {
    method: "POST",
    body: { program_id: programId, name, mode },
    errorMessage: "Не удалось сохранить название программы.",
  });
}

function renderProgramEditorRows() {
  if (!state.programRows.length) {
    return '<div class="technical-empty">Программы не найдены.</div>';
  }

  return state.programRows
    .map((row) => {
      // В поле — только пользовательское название. Встроенное идёт в
      // placeholder: так видно, что именно вернёт «Сбросить».
      const ownName = String(row.own_name || "");
      const baseName = String(row.base_program_name || "");

      return `
        <form class="object-editor-row" data-program-editor-form>
          <input type="hidden" name="program_id" value="${escapeHtml(row.program_id)}">
          <div class="object-editor-row-meta">
            <div class="object-editor-row-identity">
              <span class="object-editor-token">Программа ${escapeHtml(row.program_id)}</span>
            </div>
          </div>
          <div class="object-editor-row-controls">
            <input
              class="object-editor-name-input"
              type="text"
              name="program_name"
              value="${escapeHtml(ownName)}"
              placeholder="${escapeHtml(baseName)}"
              autocomplete="off"
              spellcheck="false"
            >
          </div>
          <div class="object-editor-row-actions">
            <button type="submit" class="object-editor-row-button object-editor-row-button--primary">Сохранить</button>
            <button
              type="button"
              class="ghost object-editor-row-button"
              data-program-editor-reset
              data-program-id="${escapeHtml(row.program_id)}"
              ${row.is_custom_name ? "" : "disabled"}
            >
              Сбросить
            </button>
          </div>
        </form>
      `;
    })
    .join("");
}

function closeAddObjectDialog() {
  const dialog = objectEditorRoot.querySelector("[data-object-editor-create]");
  if (dialog) {
    dialog.hidden = true;
  }
}

function openAddObjectDialog() {
  const dialog = objectEditorRoot.querySelector("[data-object-editor-create]");
  if (!dialog) {
    return;
  }
  dialog.hidden = false;
  const { channel: defaultChannel, objectId: defaultObjectId } = findFirstAvailableObjectSlot();
  const channelField = dialog.querySelector('input[name="channel"]');
  const objectIdField = dialog.querySelector('input[name="object_id"]');
  const nameField = dialog.querySelector('input[name="object_name"]');
  if (channelField) {
    channelField.value = String(defaultChannel);
    syncObjectEditorChoiceGroup(dialog, "channel", defaultChannel);
  }
  if (objectIdField) {
    objectIdField.value = String(defaultObjectId);
    syncObjectEditorChoiceGroup(dialog, "object_id", defaultObjectId);
  }
  if (nameField) {
    nameField.value = "";
    window.setTimeout(() => nameField.focus(), 0);
  }
  updateAddObjectDialogState(dialog);
}

function getAddObjectDialogState(dialog) {
  if (!dialog) {
    return {
      blocked: true,
      severity: "error",
      message: "Не удалось открыть форму добавления объекта.",
    };
  }

  const channel = Number(dialog.querySelector('input[name="channel"]')?.value || 0);
  const objectId = Number(dialog.querySelector('input[name="object_id"]')?.value || 0);
  const objectName = normalizeObjectName(dialog.querySelector('input[name="object_name"]')?.value || "");

  if (!Number.isInteger(channel) || channel < 1) {
    return {
      blocked: true,
      severity: "error",
      message: "Выбери поток.",
    };
  }

  if (!Number.isInteger(objectId) || objectId < 1 || objectId > 30) {
    return {
      blocked: true,
      severity: "error",
      message: "Выбери object id в диапазоне от 1 до 30.",
    };
  }

  const existingRow = findObjectEditorRow(channel, objectId);
  if (existingRow) {
    return {
      blocked: true,
      severity: "error",
      message: `Для потока «${channelLabelFor(channel)}» и object id ${objectId} уже есть запись «${existingRow.object_name}».`,
    };
  }

  if (!objectName) {
    return {
      blocked: true,
      severity: "ok",
      message: "Слот свободен. Введи название объекта, чтобы добавить запись.",
    };
  }

  if (objectName.length > 120) {
    return {
      blocked: true,
      severity: "error",
      message: "Название объекта не должно быть длиннее 120 символов.",
    };
  }

  const duplicateNameRow = findDuplicateObjectName(channel, objectId, objectName);
  if (duplicateNameRow) {
    return {
      blocked: false,
      severity: "warning",
      message:
        `Такое название уже используется в потоке «${channelLabelFor(duplicateNameRow.channel)}», object id ${duplicateNameRow.object_id}. ` +
        "Проверь, что дублирование действительно нужно.",
    };
  }

  if (/^объект\s+\d+$/i.test(objectName)) {
    return {
      blocked: false,
      severity: "warning",
      message: "Название выглядит как технический fallback. Лучше указать понятное имя объекта.",
    };
  }

  return {
    blocked: false,
    severity: "ok",
    message: "Запись будет добавлена в wash_object_names.json.",
  };
}

function updateAddObjectDialogState(dialog) {
  if (!dialog) {
    return;
  }

  const stateInfo = getAddObjectDialogState(dialog);
  const feedback = dialog.querySelector("[data-object-editor-create-feedback]");
  const saveButton = dialog.querySelector('[data-object-editor-create-save]');
  const panel = dialog.querySelector(".object-editor-create-panel");
  if (feedback) {
    feedback.textContent = stateInfo.message;
    feedback.dataset.state = stateInfo.severity;
  }
  if (panel) {
    panel.dataset.state = stateInfo.severity;
  }
  if (saveButton) {
    saveButton.disabled = stateInfo.blocked;
  }
}

export function openObjectEditor() {
  beforeOpen();

  objectEditorRoot.hidden = false;
  syncOverlayState();
  objectEditorRoot.innerHTML = `
    <div class="object-editor-backdrop" data-close-object-editor></div>
    <section class="object-editor-panel" role="dialog" aria-modal="true" aria-label="Редактор названий">
      <header class="object-editor-header">
        <div>
          <h2>Редактор названий</h2>
          <p class="object-editor-copy" data-editor-copy>Названия объектов по потокам.</p>
        </div>
        <div class="object-editor-header-actions">
          <button type="button" class="chart-modal-icon-button chart-modal-icon-button--danger" data-close-object-editor aria-label="Закрыть редактор названий" title="Закрыть">
            <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">
              <path d="M5 5L15 15"></path>
              <path d="M15 5L5 15"></path>
            </svg>
          </button>
        </div>
      </header>
      <div class="object-editor-tabs" role="tablist">
        <button type="button" class="object-editor-tab is-active" data-editor-tab="objects" role="tab" aria-selected="true">Объекты</button>
        <button type="button" class="object-editor-tab" data-editor-tab="programs" role="tab" aria-selected="false">Программы</button>
      </div>
      <div class="object-editor-list" id="objectEditorList" data-editor-panel="objects">${renderObjectEditorRows()}</div>
      <div class="object-editor-list" id="programEditorList" data-editor-panel="programs" hidden></div>
      <footer class="object-editor-footer" data-editor-panel="objects">
        <button type="button" class="object-editor-toolbar-button object-editor-toolbar-button--success" data-open-add-object>Добавить объект</button>
      </footer>
      <div class="object-editor-create" data-object-editor-create hidden>
        <div class="object-editor-create-backdrop" data-close-add-object></div>
        <section class="object-editor-create-panel" role="dialog" aria-modal="true" aria-label="Добавить объект" data-state="neutral">
          <header class="object-editor-create-header">
            <div class="eyebrow">Новая запись</div>
          </header>
          <form class="object-editor-create-form" data-object-editor-add-form>
            <input type="hidden" name="channel" value="1">
            <input type="hidden" name="object_id" value="1">
            <label class="object-editor-label object-editor-label--grow">
              <span>Поток</span>
              <div class="object-editor-choice-grid object-editor-choice-grid--channels">
                ${renderObjectEditorChannelChoices(1)}
              </div>
            </label>
            <label class="object-editor-label object-editor-label--grow">
              <span>Object ID</span>
              <div class="object-editor-choice-grid object-editor-choice-grid--ids">
                ${renderObjectEditorIdChoices(1)}
              </div>
            </label>
            <label class="object-editor-label object-editor-label--grow">
              <span>Название</span>
              <input
                type="text"
                name="object_name"
                placeholder="Название объекта"
                autocomplete="off"
                spellcheck="false"
                required
              >
            </label>
            <div class="object-editor-create-feedback" data-object-editor-create-feedback data-state="ok">
              Слот свободен. Введи название объекта, чтобы добавить запись.
            </div>
            <div class="object-editor-create-actions">
              <button type="submit" class="object-editor-toolbar-button object-editor-toolbar-button--primary" data-object-editor-create-save>Сохранить</button>
              <button type="button" class="object-editor-toolbar-button object-editor-toolbar-button--danger" data-close-add-object>Отмена</button>
            </div>
          </form>
        </section>
      </div>
    </section>
  `;

  objectEditorRoot.querySelectorAll("[data-close-object-editor]").forEach((element) => {
    element.addEventListener("click", closeObjectEditor);
  });

  const listRoot = objectEditorRoot.querySelector("#objectEditorList");
  const programRoot = objectEditorRoot.querySelector("#programEditorList");
  const addDialog = objectEditorRoot.querySelector("[data-object-editor-create]");

  function renderProgramEditor() {
    if (programRoot) {
      programRoot.innerHTML = renderProgramEditorRows();
    }
  }

  function setProgramEditorBusy(busy) {
    if (programRoot) {
      programRoot.dataset.busy = busy ? "1" : "";
    }
  }

  // Строки приходят с сервера: какие номера программ показывать и что из них
  // встречалось в данных, знает он.
  let programRowsLoaded = false;

  async function ensureProgramRows(force = false) {
    if (programRowsLoaded && !force) {
      return;
    }
    setProgramEditorBusy(true);
    try {
      const payload = await apiJson("/api/program-names", {
        errorMessage: "Не удалось загрузить названия программ.",
      });
      replaceProgramRows(payload?.program_rows);
      programRowsLoaded = true;
    } catch (error) {
      showToast(
        error instanceof Error ? error.message : "Не удалось загрузить названия программ.",
        "error"
      );
    } finally {
      setProgramEditorBusy(false);
      renderProgramEditor();
    }
  }

  function switchEditorTab(tab) {
    const nextTab = tab === "programs" ? "programs" : "objects";
    state.editorTab = nextTab;

    objectEditorRoot.querySelectorAll("[data-editor-tab]").forEach((element) => {
      const isActive = element.dataset.editorTab === nextTab;
      element.classList.toggle("is-active", isActive);
      element.setAttribute("aria-selected", isActive ? "true" : "false");
    });
    objectEditorRoot.querySelectorAll("[data-editor-panel]").forEach((element) => {
      element.hidden = element.dataset.editorPanel !== nextTab;
    });

    const copy = objectEditorRoot.querySelector("[data-editor-copy]");
    if (copy) {
      copy.textContent =
        nextTab === "programs"
          ? "Названия программ мойки."
          : "Названия объектов по потокам.";
    }

    if (nextTab === "programs") {
      renderProgramEditor();
      ensureProgramRows();
    }
  }

  objectEditorRoot.querySelectorAll("[data-editor-tab]").forEach((element) => {
    element.addEventListener("click", () => switchEditorTab(element.dataset.editorTab));
  });
  switchEditorTab(state.editorTab);

  objectEditorRoot.querySelectorAll("[data-open-add-object]").forEach((element) => {
    element.addEventListener("click", openAddObjectDialog);
  });
  objectEditorRoot.querySelectorAll("[data-close-add-object]").forEach((element) => {
    element.addEventListener("click", closeAddObjectDialog);
  });
  addDialog?.querySelector('input[name="object_name"]')?.addEventListener("input", () => {
    updateAddObjectDialogState(addDialog);
  });

  objectEditorRoot.onsubmit = async (event) => {
    event.preventDefault();
    const form = event.target.closest("form");
    if (!form) {
      return;
    }

    if (form.matches("[data-object-editor-add-form]")) {
      const formData = new FormData(form);
      const channel = Number(formData.get("channel") || 0);
      const objectId = Number(formData.get("object_id") || 0);
      const objectName = normalizeObjectName(formData.get("object_name") || "");
      const submitButton = form.querySelector('button[type="submit"]');
      const originalLabel = submitButton?.textContent || "Добавить";

      const validation = getAddObjectDialogState(addDialog);
      if (validation.blocked) {
        updateAddObjectDialogState(addDialog);
        showToast(validation.message, "error");
        return;
      }

      if (submitButton) {
        submitButton.disabled = true;
        submitButton.textContent = "Добавляю...";
      }

      try {
        const payload = await persistObjectName(channel, objectId, objectName, "create");
        applyObjectNameToWashData(payload.channel, payload.object_id, payload.object_name);
        if (Array.isArray(payload.object_rows)) {
          replaceObjectRows(payload.object_rows);
        }
        if (listRoot) {
          listRoot.innerHTML = renderObjectEditorRows();
        }
        closeAddObjectDialog();
        showToast("Объект добавлен", "success");
      } catch (error) {
        updateAddObjectDialogState(addDialog);
        showToast(error instanceof Error ? error.message : "Не удалось добавить объект в JSON.", "error");
      } finally {
        if (submitButton) {
          submitButton.disabled = false;
          submitButton.textContent = originalLabel;
          updateAddObjectDialogState(addDialog);
        }
      }
      return;
    }

    if (form.matches("[data-program-editor-form]")) {
      const formData = new FormData(form);
      const programId = Number(formData.get("program_id") || 0);
      const programName = String(formData.get("program_name") || "").trim();
      const submitButton = form.querySelector('button[type="submit"]');
      const originalLabel = submitButton?.textContent || "Сохранить";

      // Пустое поле — это не «сохранить пустоту», а отказ от собственного
      // названия: то же, что «Сбросить». Иначе сервер вернул бы 400.
      const mode = programName ? "set" : "reset";

      if (submitButton) {
        submitButton.disabled = true;
        submitButton.textContent = "Сохраняю...";
      }

      try {
        const payload = await persistProgramName(programId, programName, mode);
        replaceProgramRows(payload?.program_rows);
        renderProgramEditor();
        // Журнал держит названия в строках: перечитываем данные, иначе список
        // моек показывал бы прежнее название до следующего обновления.
        await hydrateWorkspaceData({ keepUi: true });
        showToast(mode === "set" ? "Название сохранено" : "Название сброшено", "success");
      } catch (error) {
        showToast(
          error instanceof Error ? error.message : "Не удалось сохранить название программы.",
          "error"
        );
      } finally {
        if (submitButton) {
          submitButton.disabled = false;
          submitButton.textContent = originalLabel;
        }
      }
      return;
    }

    if (!form.matches("[data-object-editor-form]")) {
      return;
    }

    const formData = new FormData(form);
    const channel = Number(formData.get("channel") || 0);
    const objectId = Number(formData.get("object_id") || 0);
    const objectName = normalizeObjectName(formData.get("object_name") || "");
    const submitButton = form.querySelector('button[type="submit"]');
    const originalLabel = submitButton?.textContent || "Сохранить";

    if (submitButton) {
      submitButton.disabled = true;
      submitButton.textContent = "Сохраняю...";
    }

    try {
      const payload = await persistObjectName(channel, objectId, objectName, "set");
      applyObjectNameToWashData(payload.channel, payload.object_id, payload.object_name);
      if (Array.isArray(payload.object_rows)) {
        replaceObjectRows(payload.object_rows);
      }
      if (listRoot) {
        listRoot.innerHTML = renderObjectEditorRows();
      }
      showToast("Название сохранено", "success");
    } catch (error) {
      showToast(error instanceof Error ? error.message : "Не удалось сохранить название объекта.", "error");
    } finally {
      if (submitButton) {
        submitButton.disabled = false;
        submitButton.textContent = originalLabel;
      }
    }
  };

  objectEditorRoot.onclick = async (event) => {
    const programResetButton = event.target.closest("[data-program-editor-reset]");
    if (programResetButton) {
      const programId = Number(programResetButton.dataset.programId || 0);
      const originalLabel = programResetButton.textContent || "Сбросить";
      programResetButton.disabled = true;
      programResetButton.textContent = "Сбрасываю...";

      try {
        const payload = await persistProgramName(programId, "", "reset");
        replaceProgramRows(payload?.program_rows);
        renderProgramEditor();
        await hydrateWorkspaceData({ keepUi: true });
        showToast("Название сброшено", "success");
      } catch (error) {
        programResetButton.disabled = false;
        programResetButton.textContent = originalLabel;
        showToast(
          error instanceof Error ? error.message : "Не удалось сбросить название программы.",
          "error"
        );
      }
      return;
    }

    const choiceButton = event.target.closest("[data-choice-group]");
    if (choiceButton) {
      const dialog = choiceButton.closest("[data-object-editor-create]");
      if (dialog) {
        const groupName = String(choiceButton.dataset.choiceGroup || "");
        const nextValue = String(choiceButton.dataset.choiceValue || "");
        const targetField = dialog.querySelector(`input[name="${groupName}"]`);
        if (targetField) {
          targetField.value = nextValue;
        }
        syncObjectEditorChoiceGroup(dialog, groupName, nextValue);
        updateAddObjectDialogState(dialog);
      }
      return;
    }

    const button = event.target.closest("[data-object-editor-reset]");
    if (!button) {
      return;
    }

    const channel = Number(button.dataset.channel || 0);
    const objectId = Number(button.dataset.objectId || 0);
    const objectName = String(button.dataset.objectName || "");
    const confirmed = window.confirm(`Сбросить пользовательское имя для объекта «${objectName}»?`);
    if (!confirmed) {
      return;
    }

    const originalLabel = button.textContent || "Сбросить";
    button.disabled = true;
    button.textContent = "Сбрасываю...";
    try {
      const payload = await persistObjectName(channel, objectId, "", "reset");
      applyObjectNameToWashData(payload.channel, payload.object_id, payload.object_name);
      if (Array.isArray(payload.object_rows)) {
        replaceObjectRows(payload.object_rows);
      }
      if (listRoot) {
        listRoot.innerHTML = renderObjectEditorRows();
      }
      showToast("Название сброшено", "success");
    } catch (error) {
      showToast(error instanceof Error ? error.message : "Не удалось сбросить название объекта.", "error");
    } finally {
      button.disabled = false;
      button.textContent = originalLabel;
    }
  };
}
