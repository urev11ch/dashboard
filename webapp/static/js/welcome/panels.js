// Сохранённые FTP-панели: переименование, переход «графики ↔ WebView панели».


// Запоминает контекст панели для экрана графиков (быстрый переход графики→веб).
function rememberPanelContext(host, scheme, label) {
  try {
    sessionStorage.setItem(
      "opticip.panel",
      JSON.stringify({ host, scheme: scheme || "http", label: label || host }),
    );
  } catch (_e) {
    /* sessionStorage недоступен — не критично */
  }
}

// WebView панели: открывает EasyWeb (/app/dashboard) топ-левел (НЕ iframe —
// EasyWeb шлёт X-Frame-Options и запрещает встраивание). По умолчанию — в
// СИСТЕМНОМ БРАУЗЕРЕ (легче: отдельный GPU-процесс, не второй встроенный
// WebView2 рядом с приложением → меньше тормозит). mode="window" — встроенное
// окно (десктоп, мост open_panel_window). Схема http/https — из обнаружения.
export function openPanelWebView(host, scheme, displayName, mode) {
  const url = `${scheme || "http"}://${host}/app/dashboard`;
  const api = window.pywebview && window.pywebview.api;
  if (mode === "window" && api && typeof api.open_panel_window === "function") {
    api.open_panel_window({ url, title: `WebView — ${displayName || host}` });
    return;
  }
  // По умолчанию — системный браузер.
  if (api && typeof api.open_external === "function") {
    api.open_external({ url });
  } else {
    // Веб: именованная вкладка per-host — повторный клик переиспользует её.
    const name = `opticip_webview_${host.replace(/[^\w]/g, "_")}`;
    window.open(url, name);
  }
}

// «Графики» подключённой панели — обычный сабмит формы (открывает рабочую
// область). Здесь только запоминаем контекст панели для кнопки WebView на
// экране графиков (быстрый переход графики→веб).
export function initPanelGraphsContext() {
  document.querySelectorAll("[data-panel-graphs]").forEach((form) => {
    form.addEventListener("submit", () =>
      rememberPanelContext(
        form.dataset.host || "",
        form.dataset.scheme || "http",
        form.dataset.label || "",
      ),
    );
  });
  // Смена источника на папку или сброс/отключение — контекст панели больше не
  // актуален, иначе кнопка WebView на экране графиков покажется на папочной
  // области и поведёт на прошлую панель.
  document
    .querySelectorAll('form[action="/workspace/open"], form[action="/workspace/reset"]')
    .forEach((form) => {
      form.addEventListener("submit", () => {
        try {
          sessionStorage.removeItem("opticip.panel");
        } catch (_e) {
          /* sessionStorage недоступен — не критично */
        }
      });
    });
}

// Клик по WebView: обычный — во встроенном окне приложения; Ctrl/Cmd+клик — в
// системном браузере. mode "window" → окно, иначе → браузер.
const webViewClickMode = (event) =>
  event.ctrlKey || event.metaKey ? "browser" : "window";

// Кнопка «WebView» у подключённой панели в главном меню (открывает EasyWeb).
export function initConnectedPanelWebView() {
  document.querySelectorAll("[data-panel-webview]").forEach((button) => {
    button.addEventListener("click", (event) => {
      openPanelWebView(
        button.dataset.host || "",
        button.dataset.scheme || "http",
        button.dataset.label || button.dataset.host || "",
        webViewClickMode(event),
      );
    });
  });
}

// На экране графиков — кнопка «WebView» (быстрый переход графики→веб).
// Контекст панели берём из sessionStorage (положен при открытии графиков).
export function initWashWebViewButton() {
  const button = document.querySelector("[data-wash-webview]");
  if (!button) {
    return;
  }
  let ctx = null;
  try {
    ctx = JSON.parse(sessionStorage.getItem("opticip.panel") || "null");
  } catch (_e) {
    ctx = null;
  }
  if (!ctx || !ctx.host) {
    return; // рабочая область не из панели (папка/архивы) — кнопки нет
  }
  button.hidden = false;
  button.addEventListener("click", (event) =>
    openPanelWebView(ctx.host, ctx.scheme, ctx.label, webViewClickMode(event)),
  );
}
