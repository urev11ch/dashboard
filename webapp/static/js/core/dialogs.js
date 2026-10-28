// Модальные формы с обычным POST-сабмитом (сервер отвечает редиректом на /).

// Модальная форма с обычным POST-сабмитом (серверный редирект на /): заголовок,
// скрытые поля, видимые поля и «Отмена / <submitLabel>». Клик по подложке
// закрывает окно. Тексты — только через textContent/value: могут прийти из сети.
export function openFormDialog({ action, title, hidden = [], fields = [], submitLabel }) {
  const dialog = document.createElement("dialog");
  dialog.className = "ftp-connect-modal";
  const form = document.createElement("form");
  form.method = "post";
  form.action = action;
  form.className = "ftp-connect-form";

  for (const [name, value] of hidden) {
    const input = document.createElement("input");
    input.type = "hidden";
    input.name = name;
    input.value = value;
    form.append(input);
  }

  const titleEl = document.createElement("div");
  titleEl.className = "ftp-connect-title";
  titleEl.textContent = title;
  form.append(titleEl);

  let focusInput = null;
  let selectFocused = false;
  for (const field of fields) {
    const label = document.createElement("label");
    label.className = "ftp-connect-field";
    label.append(document.createTextNode(field.label));
    const input = document.createElement("input");
    input.type = field.type || "text";
    input.name = field.name;
    input.value = field.value || "";
    input.autocomplete = field.autocomplete || "off";
    input.required = Boolean(field.required);
    if (field.placeholder) {
      input.placeholder = field.placeholder;
    }
    label.append(input);
    form.append(label);
    if (field.focus) {
      focusInput = input;
      selectFocused = Boolean(field.select);
    }
  }

  const actions = document.createElement("div");
  actions.className = "ftp-connect-actions";
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "ghost";
  cancel.textContent = "Отмена";
  cancel.addEventListener("click", () => dialog.close());
  const submit = document.createElement("button");
  submit.type = "submit";
  submit.textContent = submitLabel;
  actions.append(cancel, submit);
  form.append(actions);

  dialog.append(form);
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) {
      dialog.close();
    }
  });
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
  focusInput?.focus();
  if (selectFocused) {
    focusInput.select();
  }
}
