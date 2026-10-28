// Тосты (всплывающие уведомления) и запуск async-обработчиков событий.
// Контейнер создаётся при загрузке модуля — до main() и гейта hasWorkspace,
// поэтому showToast работает и на экране выбора источника.

const toastRoot = document.createElement("div");
toastRoot.className = "toast-stack";
toastRoot.setAttribute("role", "status");
toastRoot.setAttribute("aria-live", "polite");
document.body.append(toastRoot);

export function showToast(message, type = "info", duration = 4000) {
  const text = String(message || "").trim();
  if (!text) {
    return;
  }
  const toast = document.createElement("div");
  toast.className = `toast toast--${type}`;
  toast.textContent = text;
  toastRoot.append(toast);
  // Запускаем анимацию появления на следующем кадре.
  requestAnimationFrame(() => toast.classList.add("is-visible"));
  let removed = false;
  const remove = () => {
    // Ручное закрытие и авто-таймер не должны сработать дважды: гасим таймер
    // и выходим, если уже удаляли.
    if (removed) {
      return;
    }
    removed = true;
    window.clearTimeout(autoTimer);
    toast.classList.remove("is-visible");
    window.setTimeout(() => toast.remove(), 200);
  };
  const autoTimer = window.setTimeout(remove, duration);
  toast.addEventListener("click", remove);
}

// Запуск async-обработчика из обработчика события «fire-and-forget»:
// гарантированно гасим возможный reject (иначе unhandled rejection —
// например, если внутренний catch модалки сам бросит при рендере ошибки).
export function runHandler(promise) {
  Promise.resolve(promise).catch((error) => {
    console.error("Необработанная ошибка обработчика:", error);
  });
}
