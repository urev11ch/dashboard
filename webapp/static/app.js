(function () {
  const appState = window.__WASH_APP__ || {};

  // Обёртка над fetch с таймаутом. Без неё зависший бэкенд/FTP (TCP без RST)
  // никогда не резолвит промис: кнопка «Обновляю…»/«Сохраняю PDF…» залипает
  // навсегда (finally не срабатывает). По таймауту — abort и понятная ошибка.
  const DEFAULT_FETCH_TIMEOUT_MS = 30000;
  function fetchWithTimeout(resource, options) {
    const opts = options || {};
    const timeout =
      typeof opts.timeout === "number" ? opts.timeout : DEFAULT_FETCH_TIMEOUT_MS;
    const { timeout: _ignored, ...rest } = opts;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    return fetch(resource, { ...rest, signal: controller.signal })
      .catch((error) => {
        if (error && error.name === "AbortError") {
          throw new Error("Превышено время ожидания ответа сервера.");
        }
        throw error;
      })
      .finally(() => clearTimeout(timer));
  }

  const folderPickerButtons = Array.from(document.querySelectorAll("[data-folder-picker]"));
  const folderDefaultButtons = Array.from(document.querySelectorAll("[data-folder-default]"));
  // Подписи результата мойки (ключи/значения по умолчанию совпадают с сервером).
  const RESULT_LABEL_FIELDS = [
    { key: "completed", label: "Завершено штатно", def: "Завершено штатно" },
    { key: "check", label: "Требует проверки", def: "Требует проверки" },
  ];
  // Разделы окна настроек (боковая навигация в стиле System Settings macOS).
  const SETTINGS_PAGES = [
    { id: "general", label: "Общие", icon: "⚙" },
    { id: "ftp", label: "FTP", icon: "↻" },
    { id: "chart", label: "График", icon: "◠" },
    { id: "updates", label: "Обновления", icon: "⤓" },
    { id: "archives", label: "Архивы", icon: "▦" },
    { id: "diagnostics", label: "Диагностика", icon: "ⓘ" },
  ];
  // Норматив концентрации для поля ввода: число или пусто (не задан).
  function formatNormValue(value) {
    return value === null || value === undefined || value === "" ? "" : String(value);
  }
  // Должно совпадать с LINE_STYLE_OPTIONS в wash-chart.js и CHART_LINE_STYLE_IDS на сервере.
  const CHART_LINE_STYLE_OPTIONS = [
    { id: "solid", label: "Сплошная" },
    { id: "dashed", label: "Штриховая" },
    { id: "dashdot", label: "Штрих-пунктир" },
    { id: "dotted", label: "Точечная" },
    { id: "longdash", label: "Длинный штрих" },
  ];
  const initialJobStatus =
    appState.jobStatus && typeof appState.jobStatus === "object"
      ? appState.jobStatus
      : { active: false, status: "idle" };
  const sourceTabs = Array.from(document.querySelectorAll("[data-source-tab]"));
  const sourcePanels = Array.from(document.querySelectorAll("[data-source-panel]"));

  function hasDesktopFolderPickerApi() {
    return typeof window.pywebview?.api?.choose_folder === "function";
  }

  function setActiveWelcomeSource(source) {
    if (!sourceTabs.length || !sourcePanels.length) {
      return;
    }

    sourceTabs.forEach((button) => {
      const isActive = button.dataset.sourceTab === source;
      button.classList.toggle("is-active", isActive);
      button.classList.toggle("ghost", !isActive);
      button.setAttribute("aria-pressed", isActive ? "true" : "false");
    });

    sourcePanels.forEach((panel) => {
      panel.hidden = panel.dataset.sourcePanel !== source;
    });
  }

  function initWelcomeSourceTabs() {
    if (!sourceTabs.length || !sourcePanels.length) {
      return;
    }

    const activeTab = sourceTabs.find((button) => button.classList.contains("is-active")) || sourceTabs[0];
    setActiveWelcomeSource(activeTab?.dataset.sourceTab || "ftp");

    sourceTabs.forEach((button) => {
      button.addEventListener("click", () => {
        setActiveWelcomeSource(button.dataset.sourceTab || "ftp");
      });
    });
  }

  function submitForm(form) {
    if (!form) {
      return;
    }

    if (typeof form.requestSubmit === "function") {
      form.requestSubmit();
      return;
    }

    form.submit();
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

  function initFolderPickerButtons() {
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

  function initFolderDefaultButtons() {
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

  // Кнопка «Найти панель в сети»: скан локальной подсети по FTP (POST
  // /api/ftp/discover), список найденных хостов, клик подставляет адрес в форму.
  function initFtpDiscovery() {
    const button = document.querySelector("[data-ftp-discover]");
    if (!button) {
      return;
    }
    const root = button.closest("[data-ftp-discover-root]");
    const statusEl = root?.querySelector("[data-ftp-discover-status]");
    const resultsEl = root?.querySelector("[data-ftp-discover-results]");
    if (!root || !resultsEl) {
      return;
    }

    // Ручное добавление: кнопка скрыта по умолчанию, показывается, когда скан
    // не нашёл панелей; клик раскрывает форму «Добавить панель вручную».
    const manualBtn = document.querySelector("[data-ftp-manual]");
    const manualDetails = document.querySelector("[data-ftp-add]");
    if (manualBtn && manualDetails) {
      manualBtn.addEventListener("click", () => {
        manualDetails.hidden = false;
        manualDetails.open = true;
        manualDetails.querySelector('[name="host"]')?.focus();
      });
    }

    const setStatus = (text) => {
      if (statusEl) {
        statusEl.textContent = text || "";
      }
    };

    // Имя панели по умолчанию: «Weintek <имя>» (имя из EasyWeb/DNS, иначе host).
    const panelDisplayName = (panel) =>
      panel.name ? `Weintek ${panel.name}` : `Weintek ${panel.host}`;

    // Всплывающее окно выбранной панели: имя + пароль → «Добавить панель».
    // Панель СОХРАНЯЕТСЯ (POST /workspace/ftp-source/add) и появляется в списке;
    // подключение (веб-просмотр / графики) — отдельным шагом по «Подключиться».
    const openConnectDialog = (panel) => {
      const displayName = panelDisplayName(panel);
      const dialog = document.createElement("dialog");
      dialog.className = "ftp-connect-modal";

      const form = document.createElement("form");
      form.method = "post";
      form.action = "/workspace/ftp-source/add";
      form.className = "ftp-connect-form";
      for (const [name, value] of [
        ["host", panel.host],
        ["port", String(panel.port)],
        ["path", "/datalog"],
        ["passive", "on"],
        ["web_scheme", panel.web_scheme || ""],
      ]) {
        const hidden = document.createElement("input");
        hidden.type = "hidden";
        hidden.name = name;
        hidden.value = value;
        form.append(hidden);
      }

      const title = document.createElement("div");
      title.className = "ftp-connect-title";
      // textContent (не innerHTML): имя — недоверенные данные из сети.
      title.textContent = displayName;

      const nameLabel = document.createElement("label");
      nameLabel.className = "ftp-connect-field";
      nameLabel.append(document.createTextNode("Название панели"));
      const nameInput = document.createElement("input");
      nameInput.type = "text";
      nameInput.name = "label";
      nameInput.value = displayName; // «Weintek cMT-3C6F», можно поправить
      nameInput.autocomplete = "off";
      nameLabel.append(nameInput);

      const passLabel = document.createElement("label");
      passLabel.className = "ftp-connect-field";
      passLabel.append(document.createTextNode("Пароль"));
      const passInput = document.createElement("input");
      passInput.type = "password";
      passInput.name = "password";
      passInput.required = true;
      passInput.placeholder = "111111";
      passInput.autocomplete = "current-password";
      passLabel.append(passInput);

      const actions = document.createElement("div");
      actions.className = "ftp-connect-actions";
      const cancel = document.createElement("button");
      cancel.type = "button";
      cancel.className = "ghost";
      cancel.textContent = "Отмена";
      cancel.addEventListener("click", () => dialog.close());
      const add = document.createElement("button");
      add.type = "submit";
      add.textContent = "Добавить панель";
      actions.append(cancel, add);

      form.append(title, nameLabel, passLabel, actions);
      dialog.append(form);
      // Клик по подложке (вне формы) закрывает окно.
      dialog.addEventListener("click", (event) => {
        if (event.target === dialog) {
          dialog.close();
        }
      });
      dialog.addEventListener("close", () => dialog.remove());
      document.body.append(dialog);
      dialog.showModal();
      passInput.focus();
    };

    const renderResults = (panels) => {
      resultsEl.innerHTML = "";
      if (!panels.length) {
        resultsEl.hidden = true;
        return;
      }
      panels.forEach((panel) => {
        const item = document.createElement("li");
        item.className = "ftp-discover-panel";
        const choose = document.createElement("button");
        choose.type = "button";
        choose.className = "ftp-discover-item";
        // Формат: «Weintek cMT-3C6F (IP)». textContent — данные из сети.
        choose.textContent = panel.name
          ? `Weintek ${panel.name} (${panel.host})`
          : `Weintek (${panel.host})`;
        choose.addEventListener("click", () => openConnectDialog(panel));
        item.append(choose);
        resultsEl.append(item);
      });
      resultsEl.hidden = false;
    };

    button.addEventListener("click", async () => {
      if (button.disabled) {
        return;
      }
      button.disabled = true;
      resultsEl.hidden = true;
      resultsEl.innerHTML = "";
      setStatus("Сканирую локальную сеть…");
      try {
        const response = await fetchWithTimeout("/api/ftp/discover", {
          method: "POST",
          timeout: 30000,
        });
        if (!response.ok) {
          throw new Error("ftp-discover-failed");
        }
        const data = await response.json();
        const panels = Array.isArray(data.panels) ? data.panels : [];
        if (!panels.length) {
          if (!data.scanned) {
            setStatus("Не удалось определить локальную сеть.");
          } else if (data.ftp_hosts) {
            setStatus(
              `Панели Weintek не найдены. FTP-хостов в сети: ${data.ftp_hosts} ` +
                `(проверено ${data.scanned}). Если панель есть, но не видна — ` +
                `она в другой подсети (MAC-поиск не проходит за маршрутизатор); ` +
                `добавьте вручную.`
            );
          } else {
            setStatus(`Проверено адресов: ${data.scanned}. Панели не найдены.`);
          }
          renderResults([]);
          if (manualBtn) {
            manualBtn.hidden = false; // панелей нет — предлагаем добавить вручную
          }
          return;
        }
        setStatus("");  // список говорит сам за себя — без строки-счётчика
        if (manualBtn) {
          manualBtn.hidden = true;
        }
        renderResults(panels);
      } catch (_error) {
        // Инлайновый статус, не showToast: экран выбора источника — до гейта,
        // toastRoot ещё не создан (TDZ).
        setStatus("Не удалось выполнить поиск панели.");
        if (manualBtn) {
          manualBtn.hidden = false; // скан не удался — путь ручного добавления
        }
      } finally {
        button.disabled = false;
      }
    });
  }

  // Умная кнопка обновления на экране выбора источника. САМОДОСТАТОЧНА: экран
  // рендерится ДО гейта `if (!hasWorkspace) return`, где state/toastRoot/
  // checkForUpdates ещё в TDZ — только fetch + свои элементы. Режим «check» —
  // проверка; если есть устанавливаемое обновление, та же кнопка становится
  // «Установить обновление» (режим «install»).
  function initWelcomeUpdateButton() {
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
        const response = await fetch("/api/update-check", {
          headers: { Accept: "application/json" },
        });
        if (!response.ok) {
          throw new Error("update-check-failed");
        }
        const data = await response.json();
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
        const started = await fetch("/api/update/download", { method: "POST" });
        if (!started.ok) {
          const detail = await started.json().catch(() => ({}));
          throw new Error(detail.detail || "Не удалось начать скачивание.");
        }
        let ticks = 0;
        while (true) {
          await new Promise((resolve) => setTimeout(resolve, 500));
          const jobResp = await fetch("/api/update/job", {
            headers: { Accept: "application/json" },
          });
          if (!jobResp.ok) {
            throw new Error("Не удалось получить статус скачивания.");
          }
          const job = await jobResp.json();
          if (!job || job.status !== "running") {
            if (!job || job.status !== "ready") {
              throw new Error(job?.error || "Не удалось скачать обновление.");
            }
            break;
          }
          if (job.total > 0) {
            const pct = Math.min(100, Math.round((job.downloaded / job.total) * 100));
            setStatus(`Скачиваю обновление… ${pct}%`);
          }
          // Потолок ≈20 минут (500 мс × 2400) — не крутим опрос вечно.
          if (++ticks >= 2400) {
            throw new Error("Скачивание не завершилось за отведённое время.");
          }
        }
        const result = await window.pywebview.api.install_update();
        if (!result?.ok) {
          throw new Error(result?.error || "Не удалось запустить установщик.");
        }
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
  function openPanelWebView(host, scheme, displayName, mode) {
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
  function initPanelGraphsContext() {
    document.querySelectorAll("[data-panel-graphs]").forEach((form) => {
      form.addEventListener("submit", () =>
        rememberPanelContext(
          form.dataset.host || "",
          form.dataset.scheme || "http",
          form.dataset.label || "",
        ),
      );
    });
  }

  // Диалог переименования сохранённой панели: поле имени → сабмит на
  // /workspace/ftp-source/rename (форма, серверный редирект на /).
  function openRenameDialog(sourceId, label) {
    const dialog = document.createElement("dialog");
    dialog.className = "ftp-connect-modal";
    const form = document.createElement("form");
    form.method = "post";
    form.action = "/workspace/ftp-source/rename";
    form.className = "ftp-connect-form";

    const sid = document.createElement("input");
    sid.type = "hidden";
    sid.name = "source_id";
    sid.value = sourceId;

    const title = document.createElement("div");
    title.className = "ftp-connect-title";
    title.textContent = "Название панели";

    const nameLabel = document.createElement("label");
    nameLabel.className = "ftp-connect-field";
    nameLabel.append(document.createTextNode("Название"));
    const nameInput = document.createElement("input");
    nameInput.type = "text";
    nameInput.name = "label";
    nameInput.value = label || "";
    nameInput.autocomplete = "off";
    nameInput.required = true;
    nameLabel.append(nameInput);

    const actions = document.createElement("div");
    actions.className = "ftp-connect-actions";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "ghost";
    cancel.textContent = "Отмена";
    cancel.addEventListener("click", () => dialog.close());
    const save = document.createElement("button");
    save.type = "submit";
    save.textContent = "Сохранить";
    actions.append(cancel, save);

    form.append(sid, title, nameLabel, actions);
    dialog.append(form);
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) {
        dialog.close();
      }
    });
    dialog.addEventListener("close", () => dialog.remove());
    document.body.append(dialog);
    dialog.showModal();
    nameInput.focus();
    nameInput.select();
  }

  // Кнопка «Изменить» у сохранённой панели → диалог переименования.
  function initSavedPanelRename() {
    document.querySelectorAll("[data-panel-rename]").forEach((button) => {
      button.addEventListener("click", () => {
        openRenameDialog(button.dataset.sourceId || "", button.dataset.label || "");
      });
    });
  }

  // Кнопка «WebView» у подключённой панели в главном меню (открывает EasyWeb).
  function initConnectedPanelWebView() {
    document.querySelectorAll("[data-panel-webview]").forEach((button) => {
      button.addEventListener("click", () => {
        openPanelWebView(
          button.dataset.host || "",
          button.dataset.scheme || "http",
          button.dataset.label || button.dataset.host || "",
        );
      });
    });
  }

  // На экране графиков — кнопка «Веб-просмотр» (быстрый переход графики→веб).
  // Контекст панели берём из sessionStorage (положен при открытии графиков).
  function initWashWebViewButton() {
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
    button.addEventListener("click", () =>
      openPanelWebView(ctx.host, ctx.scheme, ctx.label),
    );
  }

  // Синхронизирует класс window-maximized с РЕАЛЬНЫМ состоянием окна (на случай
  // разворачивания средствами ОС — Aero Snap, Win+↑, перетаскивание к верху).
  async function refreshWindowMaximizedState() {
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
  function initDesktopTitlebar() {
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

  initWelcomeSourceTabs();
  initFolderPickerButtons();
  initFolderDefaultButtons();
  initFtpDiscovery();
  initPanelGraphsContext();
  initSavedPanelRename();
  initConnectedPanelWebView();
  initWashWebViewButton();
  initWelcomeUpdateButton();
  initDesktopTitlebar();

  const workspaceJobRoot = document.querySelector("[data-workspace-job]");
  const workspaceJobMessage = workspaceJobRoot?.querySelector("[data-workspace-job-message]");
  const workspaceJobTarget = workspaceJobRoot?.querySelector("[data-workspace-job-target]");
  const workspaceJobPhase = workspaceJobRoot?.querySelector("[data-workspace-job-phase]");
  const workspaceJobCount = workspaceJobRoot?.querySelector("[data-workspace-job-count]");
  const workspaceJobBar = workspaceJobRoot?.querySelector("[data-workspace-job-bar]");
  const workspaceJobItem = workspaceJobRoot?.querySelector("[data-workspace-job-item]");
  const workspaceJobCancelButton = workspaceJobRoot?.querySelector("[data-workspace-job-cancel]");

  function formatWorkspaceJobCount(status) {
    const current = Number(status.current || 0);
    const total = Number(status.total || 0);
    if (total > 0) {
      return `${Math.min(current, total)} из ${total}`;
    }
    if (current > 0) {
      return String(current);
    }
    return "Подсчитываю";
  }

  function workspaceJobProgressWidth(status) {
    const total = Number(status.total || 0);
    const current = Number(status.current || 0);
    if (total <= 0) {
      return 12;
    }
    return Math.max(12, Math.min(100, (current / total) * 100));
  }

  async function fetchWorkspaceJobStatus() {
    const response = await fetchWithTimeout("/api/workspace-job");
    if (!response.ok) {
      throw new Error("workspace-job-status-failed");
    }
    return response.json();
  }

  function isTerminalWorkspaceJobStatus(status) {
    return ["completed", "failed", "cancelled"].includes(String(status?.status || ""));
  }

  function setWorkspaceJobVisible(isVisible) {
    if (!workspaceJobRoot) {
      return;
    }
    workspaceJobRoot.hidden = !isVisible;
  }

  function updateWorkspaceJobUi(status, options = {}) {
    if (!workspaceJobRoot) {
      return;
    }

    const { keepVisible = false } = options;
    // Фоновое автообновление FTP не показывает блокирующий оверлей.
    const isBackground = Boolean(status?.background);
    setWorkspaceJobVisible(!isBackground && (keepVisible || Boolean(status?.active)));

    if (workspaceJobMessage) {
      workspaceJobMessage.textContent = status.message || "Обрабатываю источник";
    }
    if (workspaceJobTarget) {
      workspaceJobTarget.textContent = status.display_target || status.target_root || "";
    }
    if (workspaceJobPhase) {
      workspaceJobPhase.textContent = status.phase || "queued";
    }
    if (workspaceJobCount) {
      workspaceJobCount.textContent = formatWorkspaceJobCount(status);
    }
    if (workspaceJobBar) {
      workspaceJobBar.style.width = `${workspaceJobProgressWidth(status)}%`;
      const progressRoot = workspaceJobBar.parentElement;
      if (progressRoot) {
        progressRoot.setAttribute("aria-valuemax", String(Number(status.total || 0)));
        progressRoot.setAttribute("aria-valuenow", String(Number(status.current || 0)));
      }
    }
    if (workspaceJobItem) {
      workspaceJobItem.textContent = status.item || "";
      workspaceJobItem.hidden = !status.item;
    }
    if (workspaceJobCancelButton) {
      const isCancelling = status.status === "cancelling";
      const isActive = Boolean(status.active);
      workspaceJobCancelButton.disabled = !isActive || isCancelling;
      workspaceJobCancelButton.textContent = isCancelling ? "Отменяю..." : "Отменить";
    }
  }

  async function handleTerminalWorkspaceJob(status) {
    // Фоновое автообновление обрабатывается отдельным поллером (без оверлея и
    // перезагрузки), поэтому здесь его игнорируем.
    if (status.background) {
      return;
    }

    updateWorkspaceJobUi(status, { keepVisible: true });

    if (!appState.hasWorkspace) {
      window.location.reload();
      return;
    }

    if (status.status === "completed") {
      try {
        await hydrateWorkspaceData({ resetScroll: false });
        setScreenError("");
        setWorkspaceJobVisible(false);
        showToast("Данные обновлены", "success");
      } catch (_error) {
        window.location.reload();
      }
      return;
    }

    try {
      const payload = await fetchWorkspaceData();
      if (payload?.has_analysis) {
        applyWorkspacePayload(payload, { resetScroll: false });
      }
    } catch (_error) {
      // Keep the current screen data as-is when the refresh result cannot be fetched.
    }

    setScreenError(status.error || status.message || "");
    setWorkspaceJobVisible(false);

    if (status.status === "cancelled") {
      showToast("Обновление отменено", "info");
    } else {
      showToast(status.error || status.message || "Не удалось обновить данные.", "error");
    }
  }

  function initWorkspaceJobStatusFeed() {
    if (!workspaceJobRoot) {
      return {
        ensureMonitoring() {},
      };
    }

    updateWorkspaceJobUi(initialJobStatus);

    if (workspaceJobCancelButton) {
      workspaceJobCancelButton.addEventListener("click", async () => {
        if (workspaceJobCancelButton.disabled) {
          return;
        }

        workspaceJobCancelButton.disabled = true;
        workspaceJobCancelButton.textContent = "Отменяю...";

        try {
          await fetchWithTimeout("/api/workspace-job/cancel", { method: "POST" });
        } catch (_error) {
          workspaceJobCancelButton.disabled = false;
          workspaceJobCancelButton.textContent = "Отменить";
        }
      });
    }

    let eventSource = null;
    let pollTimer = 0;
    let pollingActive = false;

    const scheduleNextPoll = () => {
      pollTimer = window.setTimeout(tick, 1000);
    };

    const stopPolling = () => {
      pollingActive = false;
      if (pollTimer) {
        window.clearTimeout(pollTimer);
        pollTimer = 0;
      }
    };

    const closeStream = () => {
      eventSource?.close();
      eventSource = null;
    };

    // Следующий тик планирует только tick (первый — startPollingFallback),
    // иначе таймеры размножаются: каждый потерянный setTimeout плодит свою цепочку.
    const handleStatus = async (status) => {
      if (!status.active && isTerminalWorkspaceJobStatus(status)) {
        closeStream();
        stopPolling();
        await handleTerminalWorkspaceJob(status);
        return;
      }

      updateWorkspaceJobUi(status);
    };

    const tick = async () => {
      pollTimer = 0;
      try {
        const status = await fetchWorkspaceJobStatus();
        await handleStatus(status);
        if (!status.active || isTerminalWorkspaceJobStatus(status)) {
          pollingActive = false;
          return;
        }
      } catch (_error) {
        // Leave the current overlay state intact and retry on the next tick.
      }

      if (pollingActive) {
        scheduleNextPoll();
      }
    };

    const startPollingFallback = () => {
      if (pollingActive) {
        return;
      }
      pollingActive = true;
      scheduleNextPoll();
    };

    const startStream = () => {
      if (eventSource || typeof window.EventSource !== "function") {
        return Boolean(eventSource);
      }

      eventSource = new window.EventSource("/api/workspace-job/stream");
      eventSource.onmessage = async (event) => {
        try {
          const status = JSON.parse(event.data);
          await handleStatus(status);
        } catch (_error) {
          closeStream();
          startPollingFallback();
        }
      };
      eventSource.onerror = () => {
        closeStream();
        startPollingFallback();
      };
      return true;
    };

    // Подписываемся на поток только если задача реально выполняется при загрузке
    // страницы. Иначе уже завершённый (failed/cancelled) статус сразу считался бы
    // «терминальным» и вызывал бы window.location.reload() — бесконечная
    // перезагрузка после неудачного подключения к FTP.
    if (initialJobStatus.active) {
      startStream() || startPollingFallback();
    }

    window.addEventListener("beforeunload", () => {
      closeStream();
      if (pollTimer) {
        window.clearTimeout(pollTimer);
      }
    });

    return {
      ensureMonitoring() {
        if (!startStream()) {
          startPollingFallback();
        }
      },
    };
  }

  const workspaceJobFeed = initWorkspaceJobStatusFeed();

  if (!appState.hasWorkspace) {
    return;
  }

  // Фоновое автообновление FTP: отдельный лёгкий поллер. Когда серверная фоновая
  // задача завершается, тихо подтягиваем свежие данные — без оверлея и перезагрузки.
  // Поллер нужен только FTP-источнику (у папки фоновых задач не бывает) и только
  // при видимой вкладке.
  (function initBackgroundRefreshWatcher() {
    const POLL_MS = 15000;
    let lastHandledJobId =
      initialJobStatus?.background && initialJobStatus?.status === "completed"
        ? initialJobStatus.id || ""
        : "";
    let timer = 0;
    let stopped = false;

    const stop = () => {
      if (timer) {
        window.clearTimeout(timer);
        timer = 0;
      }
    };

    const schedule = () => {
      if (stopped || timer || document.visibilityState === "hidden") {
        return;
      }
      timer = window.setTimeout(tick, POLL_MS);
    };

    async function tick() {
      timer = 0;
      try {
        const status = await fetchWorkspaceJobStatus();
        if (
          status &&
          status.background &&
          status.status === "completed" &&
          (status.id || "") !== lastHandledJobId
        ) {
          lastHandledJobId = status.id || "";
          try {
            await hydrateWorkspaceData({ resetScroll: false, keepUi: true });
          } catch (_error) {
            // Оставляем текущие данные, если результат обновления не удалось получить.
          }
        }
      } catch (_error) {
        // Игнорируем сбой опроса и повторяем на следующем тике.
      }
      schedule();
    }

    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "hidden") {
        stop();
      } else {
        schedule();
      }
    });

    window.addEventListener("beforeunload", () => {
      stopped = true;
      stop();
    });

    // Тип источника отдаёт только /api/diagnostics: для папки поллер не запускаем.
    // Если диагностика недоступна — ведём себя как раньше и опрашиваем.
    void fetchDiagnostics()
      .then((data) => {
        if (String(data?.source_kind || "") === "folder") {
          stopped = true;
          stop();
          return;
        }
        schedule();
      })
      .catch(() => {
        schedule();
      });
  })();

  // Стартовые высоты строки/заголовка дня (см. .wash-row / .wash-day-header в
  // style.css). Реальные значения зависят от вёрстки и меряются по DOM после
  // первой отрисовки — константы нужны только до первого измерения.
  const WASH_LIST_ROW_HEIGHT_FALLBACK = 69;
  const WASH_LIST_HEADER_HEIGHT_FALLBACK = 38;
  // Ниже этой ширины строка списка становится резиновой (height: auto), поэтому
  // виртуализация с фиксированной высотой спейсеров там неприменима.
  const WASH_LIST_FLUID_LAYOUT_QUERY = "(max-width: 1100px)";
  const WASH_LIST_OVERSCAN = 8;
  const SEARCH_INPUT_DEBOUNCE_MS = 180;
  const DEFAULT_PERIOD_PRESET = "7d";
  // Потолок опроса /api/update/job: 500 мс × 2400 ≈ 20 минут.
  const UPDATE_POLL_MAX_TICKS = 2400;
  const state = {
    washRows: [],
    washRowIndexesByObjectKey: new Map(),
    objectRows: [],
    filteredRows: [],
    displayItems: [],
    displayOffsets: [0],
    displayTotalHeight: 0,
    displayVersion: 0,
    dateBounds: null,
    activePeriodPreset: DEFAULT_PERIOD_PRESET,
    // Смещение таймзоны сервера (минуты к UTC). Приходит в /api/workspace-data;
    // если поля нет — считаем дни в зоне браузера (прежнее поведение).
    serverTzOffsetMin: null,
    // Обновление: последний ответ /api/update-check и состояние скачивания.
    updateInfo: null,
    updateJob: null,
    updateTimer: null,
    // Защёлка «скачивание/установка уже идёт»: disabled у кнопки живёт лишь до
    // ближайшей перерисовки панели, а она перерисовывается на каждом опросе.
    updateBusy: false,
  };
  const washListMetrics = {
    rowHeight: WASH_LIST_ROW_HEIGHT_FALLBACK,
    headerHeight: WASH_LIST_HEADER_HEIGHT_FALLBACK,
  };
  const fluidWashListQuery =
    typeof window.matchMedia === "function" ? window.matchMedia(WASH_LIST_FLUID_LAYOUT_QUERY) : null;

  function isWashListVirtualized() {
    return !fluidWashListQuery?.matches;
  }
  const detailCache = new Map();
  const detailRequestCache = new Map();
  const chartPayloadCache = new Map();
  const chartPayloadRequestCache = new Map();
  const DETAIL_CACHE_LIMIT = 200;
  const CHART_PAYLOAD_CACHE_LIMIT = 80;
  // Поколение данных рабочей области: растёт при каждой инвалидации кэшей
  // (в том числе при фоновом автообновлении FTP). Ответ запроса, стартовавшего
  // до инвалидации, в кэш уже не попадает — иначе устаревшие детали/график
  // «воскресали» бы в только что очищенном кэше.
  let workspaceDataGeneration = 0;

  function setBoundedCacheEntry(cache, key, value, limit) {
    // Простая LRU-эвикция: не даём кэшам расти неограниченно между обновлениями данных.
    if (cache.has(key)) {
      cache.delete(key);
    }
    cache.set(key, value);
    while (cache.size > limit) {
      const oldestKey = cache.keys().next().value;
      cache.delete(oldestKey);
    }
  }
  let modalRequestId = 0;
  let restoreDocumentTitle = null;
  let washListRenderFrame = 0;
  let searchRenderTimer = 0;
  let settingsOpenId = 0;
  // Активный таймер подтверждения очистки архивов — чистится при закрытии настроек.
  let archiveCleanupInterval = null;
  let diagnosticsOpenId = 0;

  const washList = document.querySelector("#washList");
  const washFilterCount = document.querySelector("#washFilterCount");
  const searchInput = document.querySelector("#searchInput");
  const dayFilter = document.querySelector("#dayFilter");
  const openDayFilterButton = document.querySelector("#openDayFilter");
  const clearDateFiltersButton = document.querySelector("#clearDateFilters");
  const periodPresetButtons = Array.from(document.querySelectorAll("[data-period-preset]"));
  const channelFilter = document.querySelector("#channelFilter");
  const sortOrder = document.querySelector("#sortOrder");
  const openObjectEditorButton = document.querySelector("#openObjectEditor");
  const screenPath = document.querySelector("#screenPath");
  const screenStats = document.querySelector("#screenStats");
  const screenErrorNotice = document.querySelector("#screenErrorNotice");
  const workspaceRefreshForm = document.querySelector("#workspaceRefreshForm");

  if (
    !washList ||
    !washFilterCount ||
    !searchInput ||
    !dayFilter ||
    !openDayFilterButton ||
    !clearDateFiltersButton ||
    !channelFilter ||
    !sortOrder
  ) {
    return;
  }

  const modalRoot = document.createElement("div");
  modalRoot.className = "chart-modal";
  modalRoot.hidden = true;
  document.body.append(modalRoot);

  const printRoot = document.createElement("div");
  printRoot.className = "chart-print-root";
  printRoot.hidden = true;
  document.body.append(printRoot);

  const objectEditorRoot = document.createElement("div");
  objectEditorRoot.className = "object-editor-modal";
  objectEditorRoot.hidden = true;
  document.body.append(objectEditorRoot);

  const settingsRoot = document.createElement("div");
  settingsRoot.className = "object-editor-modal";
  settingsRoot.hidden = true;
  document.body.append(settingsRoot);

  const diagnosticsRoot = document.createElement("div");
  diagnosticsRoot.className = "object-editor-modal";
  diagnosticsRoot.hidden = true;
  document.body.append(diagnosticsRoot);

  // ---- Тосты (всплывающие уведомления) ----------------------------------
  const toastRoot = document.createElement("div");
  toastRoot.className = "toast-stack";
  toastRoot.setAttribute("role", "status");
  toastRoot.setAttribute("aria-live", "polite");
  document.body.append(toastRoot);

  function showToast(message, type = "info", duration = 4000) {
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
  function runHandler(promise) {
    Promise.resolve(promise).catch((error) => {
      console.error("Необработанная ошибка обработчика:", error);
    });
  }

  // ---- Часы (текущее время) ---------------------------------------------
  // Форматтеры создаём один раз: Intl-объект дорогой, а тик — каждую секунду.
  const CLOCK_DATE_FORMAT = new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  });
  const CLOCK_TIME_FORMAT = new Intl.DateTimeFormat("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });

  function initClock() {
    const clock = document.querySelector("#appClock");
    if (!clock) {
      return;
    }

    let timer = 0;

    const tick = () => {
      const now = new Date();
      clock.textContent = `${CLOCK_DATE_FORMAT.format(now)} · ${CLOCK_TIME_FORMAT.format(now)}`;
    };

    const stop = () => {
      if (timer) {
        window.clearInterval(timer);
        timer = 0;
      }
    };

    // На скрытой вкладке часы никто не видит — тик останавливаем.
    const start = () => {
      if (timer) {
        return;
      }
      tick();
      timer = window.setInterval(tick, 1000);
    };

    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "hidden") {
        stop();
      } else {
        start();
      }
    });
    window.addEventListener("beforeunload", stop);

    start();
  }

  const WASH_RESULT_PREF_KEY = "opticipShowWashResultV1";

  function isWashResultVisible() {
    try {
      return window.localStorage.getItem(WASH_RESULT_PREF_KEY) !== "0";
    } catch (error) {
      return true;
    }
  }

  function applyWashResultVisibility() {
    document.body.classList.toggle("wash-result-hidden", !isWashResultVisible());
  }

  function setWashResultVisible(visible) {
    try {
      window.localStorage.setItem(WASH_RESULT_PREF_KEY, visible ? "1" : "0");
    } catch (error) {
      /* localStorage может быть недоступен — просто применяем к текущей сессии */
    }
    applyWashResultVisibility();
  }

  // Экранирование как в wash-chart.js: включая одинарную кавычку (&#39;) —
  // данные пользовательские, разметка собирается строками.
  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function badgeClass(status, kind) {
    // Цвет по категории результата (независимо от текста подписи). Если категория
    // не передана — резерв по тексту для обратной совместимости.
    const resolved = kind || (String(status || "").startsWith("Завершено") ? "completed" : "check");
    return resolved === "completed" ? "badge ok" : "badge warn";
  }

  function formatModalDateTime(value) {
    const match = String(value ?? "").match(/^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})$/);
    if (!match) {
      return String(value ?? "—");
    }

    const [, year, month, day, hours, minutes, seconds] = match;
    return `${day}.${month}.${year}. ${hours}.${minutes}.${seconds}`;
  }

  // Возвращает сырую строку: экранирование — только в месте вставки в разметку,
  // иначе получалось двойное экранирование (&amp;quot; в тултипе).
  function formatListDateTime(value) {
    const match = String(value ?? "").match(/^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})$/);
    if (!match) {
      return String(value ?? "—");
    }

    const [, year, month, day, hours, minutes, seconds] = match;
    return `${day}.${month}.${year} ${hours}:${minutes}:${seconds}`;
  }

  function buildSearchBlob(row) {
    return [
      row.object,
      row.program,
      row.date_time,
      row.source_name,
      row.status,
      `Канал ${row.channel}`,
    ]
      .join(" ")
      .toLowerCase();
  }

  function normalizeObjectName(value) {
    return String(value ?? "")
      .replace(/\s+/g, " ")
      .trim();
  }

  function sortObjectRows(rows) {
    return rows.sort(
      (left, right) =>
        Number(left.channel) - Number(right.channel) ||
        Number(left.object_id) - Number(right.object_id)
    );
  }

  function replaceObjectRows(rows) {
    state.objectRows = sortObjectRows((Array.isArray(rows) ? rows : []).map((row) => ({ ...row })));
  }

  function buildWashObjectKey(channel, objectId) {
    return `${Number(channel) || 0}:${Number(objectId) || 0}`;
  }

  function rebuildWashRowIndexes() {
    const nextIndexes = new Map();
    state.washRows.forEach((row, index) => {
      const key = buildWashObjectKey(row.channel, row.object_id);
      if (!nextIndexes.has(key)) {
        nextIndexes.set(key, []);
      }
      nextIndexes.get(key).push(index);
    });
    state.washRowIndexesByObjectKey = nextIndexes;
  }

  function replaceWashRows(rows) {
    state.washRows = (Array.isArray(rows) ? rows : []).map((row) => {
      const nextRow = { ...row };
      if (!nextRow.search_blob) {
        nextRow.search_blob = buildSearchBlob(nextRow);
      }
      return nextRow;
    });
    state.dateBounds = null;
    rebuildWashRowIndexes();
  }

  function renderSummaryPills(summary = {}) {
    const pills = [
      `<div class="stat-pill">Моек: ${Number(summary.cycle_count || 0)}</div>`,
      `<div class="stat-pill">Объектов: ${Number(summary.object_count || 0)}</div>`,
      `<div class="stat-pill">Баз: ${Number(summary.db_count || 0)}</div>`,
    ];

    if (Number(summary.archive_count || 0) > 0) {
      pills.push(`<div class="stat-pill">Архивов: ${Number(summary.archive_count || 0)}</div>`);
    }
    if (Number(summary.ftp_source_count || 0) > 0) {
      pills.push(`<div class="stat-pill">FTP: ${Number(summary.ftp_source_count || 0)}</div>`);
    }

    return pills.join("");
  }

  function setScreenError(message) {
    if (!screenErrorNotice) {
      return;
    }
    const text = String(message || "").trim();
    screenErrorNotice.textContent = text;
    screenErrorNotice.hidden = !text;
  }

  function applyWorkspaceMeta(payload = {}) {
    if (screenPath) {
      screenPath.textContent = String(payload.display_root || appState.displayRoot || "");
    }
    if (screenStats) {
      screenStats.innerHTML = renderSummaryPills(payload.summary || {});
    }
    setScreenError(payload.error || "");
  }

  function setWashListMessage(message) {
    washList.innerHTML = `<div class="technical-empty">${escapeHtml(message)}</div>`;
  }

  // Скелетон списка моек: серые строки-заготовки в тех же колонках, что и реальные.
  function setWashListSkeleton(count = 8) {
    const row = `
      <div class="wash-row wash-row--skeleton" aria-hidden="true">
        <div class="wash-cell wash-cell--primary"><span class="skeleton skeleton-line" style="width:72%"></span></div>
        <div class="wash-cell"><span class="skeleton skeleton-line" style="width:82%"></span></div>
        <div class="wash-cell"><span class="skeleton skeleton-line" style="width:74%"></span></div>
        <div class="wash-cell"><span class="skeleton skeleton-line" style="width:48%"></span></div>
        <div class="wash-cell wash-cell--status"><span class="skeleton skeleton-badge"></span></div>
      </div>`;
    washList.innerHTML = `<div class="wash-list-skeleton" role="status" aria-label="Загрузка списка моек">${row.repeat(count)}</div>`;
  }

  function clearWorkspaceDataCaches({ keepUi = false } = {}) {
    // Новое поколение: ответы запросов, стартовавших до очистки, в кэш не попадут.
    workspaceDataGeneration += 1;
    detailCache.clear();
    detailRequestCache.clear();
    chartPayloadCache.clear();
    chartPayloadRequestCache.clear();
    // Фоновое обновление не должно закрывать открытые окна пользователя —
    // кэши инвалидируются, а UI остаётся как есть.
    if (keepUi) {
      return;
    }
    if (!modalRoot.hidden) {
      closeChartModal();
    }
    if (!objectEditorRoot.hidden) {
      closeObjectEditor();
    }
  }

  async function fetchWorkspaceData() {
    const response = await fetchWithTimeout("/api/workspace-data");
    if (!response.ok) {
      throw new Error("workspace-data-request-failed");
    }
    return response.json();
  }

  function applyWorkspacePayload(payload, { resetScroll = false } = {}) {
    appState.hasWorkspace = Boolean(payload?.has_analysis);
    appState.hasAnalysis = Boolean(payload?.has_analysis);
    appState.displayRoot = String(payload?.display_root || "");
    appState.summary = payload?.summary || {};
    appState.error = String(payload?.error || "");
    appState.jobStatus = payload?.job_status || appState.jobStatus || {};
    state.serverTzOffsetMin =
      typeof payload?.tz_offset_min === "number" && Number.isFinite(payload.tz_offset_min)
        ? payload.tz_offset_min
        : null;

    applyWorkspaceMeta(payload);
    replaceObjectRows(payload?.object_rows);
    replaceWashRows(payload?.wash_rows);
    fillChannelFilter();
    syncDateFilterBounds();
    syncDayFilterButton();
    syncPeriodPresetButtons();
    renderWashList({ resetScroll });

    if (openObjectEditorButton) {
      openObjectEditorButton.disabled = false;
    }
  }

  let workspaceHydrationToken = 0;

  async function hydrateWorkspaceData({ resetScroll = false, keepUi = false } = {}) {
    // Скелетон — только при первой загрузке (пустой список); при фоновом
    // обновлении оставляем текущие мойки, чтобы не мигало.
    if (!state.washRows.length) {
      setWashListSkeleton();
    }
    const token = ++workspaceHydrationToken;
    const payload = await fetchWorkspaceData();
    // Пока ждали ответ, стартовала более свежая гидрация — не перетираем её
    // данные устаревшим ответом.
    if (token !== workspaceHydrationToken) {
      return payload;
    }
    clearWorkspaceDataCaches({ keepUi });
    applyWorkspacePayload(payload, { resetScroll });
    return payload;
  }

  async function startWorkspaceRefresh() {
    const response = await fetchWithTimeout("/api/workspace/refresh", { method: "POST" });
    if (!response.ok) {
      let errorMessage = "Не удалось запустить обновление.";
      try {
        const payload = await response.json();
        if (payload?.detail) {
          errorMessage = String(payload.detail);
        }
      } catch (_error) {
        // Fall back to the generic message.
      }
      throw new Error(errorMessage);
    }
    return response.json();
  }

  function renderObjectEditorChannelChoices(selectedValue = 1) {
    const selectedChannel = Number(selectedValue || 1);
    return Array.from({ length: 5 }, (_, index) => index + 1)
      .map(
        (channel) => `
          <button
            type="button"
            class="object-editor-choice${channel === selectedChannel ? " is-selected" : ""}"
            data-choice-group="channel"
            data-choice-value="${channel}"
            aria-pressed="${channel === selectedChannel ? "true" : "false"}"
          >
            ${channel}
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

  function syncSortButtons() {
    const currentValue = String(sortOrder.value || "date_desc");
    document.querySelectorAll("[data-sort-value]").forEach((button) => {
      const isActive = String(button.dataset.sortValue || "") === currentValue;
      button.classList.toggle("is-active", isActive);
      button.setAttribute("aria-pressed", isActive ? "true" : "false");
    });
  }

  function syncChannelButtons() {
    const currentValue = String(channelFilter.value || "");
    document.querySelectorAll("[data-channel-value]").forEach((button) => {
      const isActive = String(button.dataset.channelValue || "") === currentValue;
      button.classList.toggle("is-active", isActive);
      button.setAttribute("aria-pressed", isActive ? "true" : "false");
    });
  }

  function getLocalDateKey(value = new Date()) {
    const date = value instanceof Date ? value : new Date(value);
    const year = String(date.getFullYear());
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  }

  // Ключ дня (YYYY-MM-DD) в таймзоне СЕРВЕРА: строки списка приходят с start_day,
  // который сервер формирует в своей зоне (format_day_key). В вебе зоны браузера
  // и сервера могут не совпадать — тогда «Сегодня» пустел или ехал на сутки.
  // Если сервер не отдал tz_offset_min, считаем по зоне браузера, как раньше.
  function getServerDateKey(value = new Date()) {
    const date = value instanceof Date ? value : new Date(value);
    if (state.serverTzOffsetMin === null) {
      return getLocalDateKey(date);
    }

    const shifted = new Date(date.getTime() + state.serverTzOffsetMin * 60000);
    const year = String(shifted.getUTCFullYear());
    const month = String(shifted.getUTCMonth() + 1).padStart(2, "0");
    const day = String(shifted.getUTCDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  }

  // Начало серверных суток (epoch, секунды) для ключа дня YYYY-MM-DD.
  function getServerDayStartTs(dayKey) {
    const parts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(dayKey || ""));
    if (!parts) {
      return null;
    }

    const [, year, month, day] = parts;
    if (state.serverTzOffsetMin === null) {
      return new Date(Number(year), Number(month) - 1, Number(day)).getTime() / 1000;
    }

    return Date.UTC(Number(year), Number(month) - 1, Number(day)) / 1000 - state.serverTzOffsetMin * 60;
  }

  function getPeriodPresetStartTs() {
    if (state.activePeriodPreset === "all" || !state.activePeriodPreset) {
      return null;
    }
    if (state.activePeriodPreset === "today") {
      return getServerDayStartTs(getServerDateKey());
    }
    if (state.activePeriodPreset === "7d") {
      return (Date.now() - 7 * 24 * 60 * 60 * 1000) / 1000;
    }
    if (state.activePeriodPreset === "30d") {
      return (Date.now() - 30 * 24 * 60 * 60 * 1000) / 1000;
    }
    return null;
  }

  function syncPeriodPresetButtons() {
    periodPresetButtons.forEach((button) => {
      button.classList.toggle("is-active", button.dataset.periodPreset === state.activePeriodPreset);
    });
  }

  function getAvailableDateBounds() {
    if (state.dateBounds) {
      return state.dateBounds;
    }

    const dates = state.washRows.map((row) => row.start_day).filter(Boolean).sort();
    state.dateBounds = {
      min: dates[0] || "",
      max: dates[dates.length - 1] || "",
    };
    return state.dateBounds;
  }

  function syncDateFilterBounds() {
    const { min, max } = getAvailableDateBounds();
    dayFilter.min = min;
    dayFilter.max = max;
  }

  function syncDayFilterButton() {
    const hasSelectedDay = Boolean(dayFilter.value);
    openDayFilterButton.classList.toggle("is-active", hasSelectedDay);
    const label = hasSelectedDay ? `Выбрана дата: ${dayFilter.value}` : "Открыть календарь";
    openDayFilterButton.title = label;
    openDayFilterButton.setAttribute("aria-label", label);
  }

  function resetAllSearchFilters() {
    if (searchRenderTimer) {
      window.clearTimeout(searchRenderTimer);
      searchRenderTimer = 0;
    }

    searchInput.value = "";
    channelFilter.value = "";
    dayFilter.value = "";
    state.activePeriodPreset = DEFAULT_PERIOD_PRESET;
    syncChannelButtons();
    syncDateFilterBounds();
    syncDayFilterButton();
    syncPeriodPresetButtons();
  }

  function getModalNavigation(key) {
    const rows = state.filteredRows.length ? state.filteredRows : getFilteredRows();
    const currentIndex = rows.findIndex((row) => row.key === key);
    if (currentIndex < 0) {
      return { previous: null, next: null };
    }

    return {
      previous: rows[currentIndex - 1] ?? null,
      next: rows[currentIndex + 1] ?? null,
    };
  }

  function fillChannelFilter() {
    const channelOptionsRoot = document.querySelector("#channelOptions");
    if (!channelOptionsRoot) {
      return;
    }

    const channels = [...new Set(state.washRows.map((item) => Number(item.channel)))]
      .filter((channel) => Number.isInteger(channel) && channel >= 1 && channel <= 5)
      .sort((a, b) => a - b);

    if (channelFilter.value && !channels.includes(Number(channelFilter.value))) {
      channelFilter.value = "";
    }

    const buttons = [
      '<button type="button" class="toolbar-channel-option" data-channel-value="">Все</button>',
      ...channels.map(
        (channel) =>
          `<button type="button" class="toolbar-channel-option" data-channel-value="${channel}">Канал ${channel}</button>`
      ),
    ];
    channelOptionsRoot.innerHTML = buttons.join("");

    channelOptionsRoot.querySelectorAll("[data-channel-value]").forEach((button) => {
      button.addEventListener("click", () => {
        const nextValue = String(button.dataset.channelValue || "");
        if (channelFilter.value === nextValue) {
          return;
        }
        channelFilter.value = nextValue;
        syncChannelButtons();
        renderWashList({ resetScroll: true });
      });
    });

    syncChannelButtons();
  }

  function sortRows(rows) {
    const mode = sortOrder.value;
    const sorted = [...rows];

    sorted.sort((left, right) => {
      const leftStartTs = Number(left.start_ts || 0);
      const rightStartTs = Number(right.start_ts || 0);

      if (mode === "date_asc") {
        return leftStartTs - rightStartTs;
      }

      if (mode === "object_asc") {
        return String(left.object || "").localeCompare(String(right.object || ""), "ru") || rightStartTs - leftStartTs;
      }

      if (mode === "duration_desc") {
        return Number(right.duration_seconds || 0) - Number(left.duration_seconds || 0) || rightStartTs - leftStartTs;
      }

      return rightStartTs - leftStartTs;
    });

    return sorted;
  }

  function getFilteredRows() {
    const query = searchInput.value.trim().toLowerCase();
    const selectedDay = dayFilter.value;
    const channel = channelFilter.value;
    const presetStartTs = getPeriodPresetStartTs();
    const todayKey = getServerDateKey();

    const filtered = state.washRows.filter((row) => {
      if (selectedDay && row.start_day !== selectedDay) {
        return false;
      }

      if (!selectedDay && state.activePeriodPreset === "today" && row.start_day !== todayKey) {
        return false;
      }

      if (!selectedDay && presetStartTs !== null && state.activePeriodPreset !== "today") {
        if (Number(row.start_ts || 0) < presetStartTs) {
          return false;
        }
      }

      if (channel && String(row.channel) !== channel) {
        return false;
      }

      if (!query) {
        return true;
      }

      return String(row.search_blob || "").includes(query);
    });

    return sortRows(filtered);
  }

  function renderWashRow(row) {
    const dateTime = escapeHtml(formatListDateTime(row.date_time));
    return `
      <div class="wash-row" data-key="${escapeHtml(row.key)}" role="button" tabindex="0">
        <div class="wash-cell wash-cell--primary">
          <div class="wash-entry-time" title="${dateTime}">${dateTime}</div>
        </div>
        <div class="wash-cell">
          <div class="wash-entry-object" title="${escapeHtml(row.object)}">${escapeHtml(row.object)}</div>
        </div>
        <div class="wash-cell">
          <div class="wash-entry-program" title="${escapeHtml(row.program)}">${escapeHtml(row.program)}</div>
        </div>
        <div class="wash-cell">
          <span class="wash-chip" title="${escapeHtml(row.duration)}">${escapeHtml(row.duration)}</span>
        </div>
        <div class="wash-cell wash-cell--status">
          <span class="${badgeClass(row.status, row.result_kind)}" title="${escapeHtml(row.status)}">${escapeHtml(row.status)}</span>
          ${row.concentration_kind === "low" ? '<span class="conc-chip conc-chip--low" title="Концентрация раствора ниже нормы">конц. ↓</span>' : ""}
          <button type="button" class="wash-row-pdf-button" data-download-row-pdf title="PDF" aria-label="PDF">PDF</button>
        </div>
      </div>
    `;
  }

  // Границы последнего отрисованного окна. Пока они не изменились, DOM не
  // трогаем: раньше innerHTML переписывался на каждом кадре скролла (полный
  // reparse видимых строк — слетали hover и выделение текста).
  const renderedWindow = { version: -1, start: -1, end: -1, virtualized: false };

  function invalidateRenderedWashWindow() {
    renderedWindow.version = -1;
    renderedWindow.start = -1;
    renderedWindow.end = -1;
    renderedWindow.virtualized = false;
  }

  function renderWashItemsHtml(startIndex, endIndex) {
    const items = state.displayItems;
    const chunks = [];
    for (let i = startIndex; i < endIndex; i += 1) {
      const item = items[i];
      chunks.push(
        item.type === "header"
          ? `<div class="wash-day-header" aria-hidden="true">${escapeHtml(item.label)}</div>`
          : renderWashRow(item.row)
      );
    }
    return chunks.join("");
  }

  function writeWashListHtml(html) {
    // innerHTML уничтожает сфокусированную строку — запоминаем её ключ и после
    // перерисовки возвращаем фокус клавиатуры на ту же строку, если она в DOM.
    const activeElement = document.activeElement;
    const focusedRowKey =
      activeElement && washList.contains(activeElement)
        ? activeElement.closest("[data-key]")?.dataset.key || ""
        : "";

    washList.innerHTML = html;

    if (focusedRowKey) {
      const rowToFocus = Array.from(washList.querySelectorAll("[data-key]")).find(
        (row) => row.dataset.key === focusedRowKey
      );
      rowToFocus?.focus({ preventScroll: true });
    }
  }

  // Реальные высоты строки и заголовка дня берём из DOM: они заданы в CSS и
  // меняются вместе с медиа-запросами/настройками, а не константой в JS.
  function syncWashListMetricsFromDom() {
    let changed = false;

    const rowElement = washList.querySelector(".wash-row:not(.wash-row--skeleton)");
    if (rowElement) {
      const height = rowElement.getBoundingClientRect().height;
      if (height > 0 && Math.abs(height - washListMetrics.rowHeight) > 0.5) {
        washListMetrics.rowHeight = height;
        changed = true;
      }
    }

    const headerElement = washList.querySelector(".wash-day-header");
    if (headerElement) {
      const height = headerElement.getBoundingClientRect().height;
      if (height > 0 && Math.abs(height - washListMetrics.headerHeight) > 0.5) {
        washListMetrics.headerHeight = height;
        changed = true;
      }
    }

    return changed;
  }

  let washListMetricsResyncing = false;

  function renderVirtualizedWashList({ resetScroll = false } = {}) {
    if (resetScroll) {
      washList.scrollTop = 0;
    }

    const items = state.displayItems;
    if (!items.length) {
      writeWashListHtml('<div class="technical-empty">Мойки не найдены</div>');
      invalidateRenderedWashWindow();
      return;
    }

    // Ниже 1100px строка резиновая (height: auto, min-height: 69px), высота
    // заранее неизвестна и у разных строк разная — спейсеры с фиксированной
    // высотой давали бы провалы и недолистывание. В этом режиме виртуализацию
    // отключаем и рисуем список целиком.
    if (!isWashListVirtualized()) {
      if (
        renderedWindow.virtualized ||
        renderedWindow.version !== state.displayVersion ||
        renderedWindow.start !== 0 ||
        renderedWindow.end !== items.length
      ) {
        writeWashListHtml(renderWashItemsHtml(0, items.length));
        renderedWindow.version = state.displayVersion;
        renderedWindow.start = 0;
        renderedWindow.end = items.length;
        renderedWindow.virtualized = false;
      }
      return;
    }

    const offsets = state.displayOffsets;
    const totalHeight = state.displayTotalHeight;
    const rowHeight = washListMetrics.rowHeight;
    const viewportHeight = Math.max(washList.clientHeight, rowHeight);
    const scrollTop = washList.scrollTop;
    const overscanPx = WASH_LIST_OVERSCAN * rowHeight;
    const windowTop = Math.max(0, scrollTop - overscanPx);
    const windowBottom = scrollTop + viewportHeight + overscanPx;

    const startIndex = findFirstVisibleItem(offsets, windowTop);
    let endIndex = startIndex;
    while (endIndex < items.length && offsets[endIndex] < windowBottom) {
      endIndex += 1;
    }

    if (
      renderedWindow.virtualized &&
      renderedWindow.version === state.displayVersion &&
      renderedWindow.start === startIndex &&
      renderedWindow.end === endIndex
    ) {
      return;
    }

    const topSpacerHeight = offsets[startIndex];
    const bottomSpacerHeight = Math.max(0, totalHeight - offsets[endIndex]);

    const chunks = [];
    if (topSpacerHeight) {
      chunks.push(`<div class="wash-list-spacer" style="height:${topSpacerHeight}px" aria-hidden="true"></div>`);
    }
    chunks.push(renderWashItemsHtml(startIndex, endIndex));
    if (bottomSpacerHeight) {
      chunks.push(`<div class="wash-list-spacer" style="height:${bottomSpacerHeight}px" aria-hidden="true"></div>`);
    }

    writeWashListHtml(chunks.join(""));
    renderedWindow.version = state.displayVersion;
    renderedWindow.start = startIndex;
    renderedWindow.end = endIndex;
    renderedWindow.virtualized = true;

    // Первая отрисовка (или смена вёрстки) — уточняем высоты по факту и, если
    // они разошлись с текущими, пересчитываем смещения и рисуем окно заново.
    if (!washListMetricsResyncing && syncWashListMetricsFromDom()) {
      washListMetricsResyncing = true;
      try {
        buildWashDisplayItems();
        renderVirtualizedWashList();
      } finally {
        washListMetricsResyncing = false;
      }
    }
  }

  function scheduleVirtualizedWashList() {
    if (washListRenderFrame) {
      return;
    }
    washListRenderFrame = window.requestAnimationFrame(() => {
      washListRenderFrame = 0;
      renderVirtualizedWashList();
    });
  }

  function updateWashFilterCount(count) {
    if (!washFilterCount) {
      return;
    }
    washFilterCount.textContent = `Найдено моек: ${count}`;
  }

  function renderWashList(options = {}) {
    const { resetScroll = true } = options;
    state.filteredRows = getFilteredRows();
    updateWashFilterCount(state.filteredRows.length);
    buildWashDisplayItems();
    renderVirtualizedWashList({ resetScroll });
  }

  function formatDayHeader(day) {
    const parts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(day || ""));
    if (!parts) {
      return "Без даты";
    }
    const date = new Date(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3]));
    return date.toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" });
  }

  // Плоский список элементов для виртуализации: строки + (при сортировке по дате)
  // заголовки-разделители по дням. Считаем префикс-суммы смещений для смешанных высот.
  function buildWashDisplayItems() {
    const rows = state.filteredRows;
    const groupByDay = sortOrder && (sortOrder.value === "date_desc" || sortOrder.value === "date_asc");
    const items = [];
    let lastDay = null;
    for (const row of rows) {
      if (groupByDay) {
        const day = row.start_day || "";
        if (day !== lastDay) {
          lastDay = day;
          items.push({
            type: "header",
            label: formatDayHeader(day),
            height: washListMetrics.headerHeight,
          });
        }
      }
      items.push({ type: "row", row, height: washListMetrics.rowHeight });
    }

    const offsets = new Array(items.length + 1);
    offsets[0] = 0;
    for (let i = 0; i < items.length; i += 1) {
      offsets[i + 1] = offsets[i] + items[i].height;
    }
    state.displayItems = items;
    state.displayOffsets = offsets;
    state.displayTotalHeight = offsets[items.length];
    // Версия набора элементов: по ней кэш отрисованного окна понимает, что
    // список изменился и перерисовка нужна даже при тех же границах.
    state.displayVersion += 1;
  }

  // Первый элемент, нижняя граница которого больше value (бинарный поиск по offsets).
  function findFirstVisibleItem(offsets, value) {
    let lo = 0;
    let hi = offsets.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (offsets[mid + 1] <= value) {
        lo = mid + 1;
      } else {
        hi = mid;
      }
    }
    return lo;
  }

  async function getDetail(key) {
    if (detailCache.has(key)) {
      return detailCache.get(key);
    }

    if (detailRequestCache.has(key)) {
      return detailRequestCache.get(key);
    }

    const generation = workspaceDataGeneration;
    const request = fetchWithTimeout(`/api/wash-details?key=${encodeURIComponent(key)}`)
      .then((response) => {
        if (!response.ok) {
          throw new Error("wash-details-request-failed");
        }
        return response.json();
      })
      .then((payload) => {
        // Пока ответ летел, данные могли обновиться (фоновый FTP) и кэши —
        // очиститься: устаревший ответ в новый кэш не кладём.
        if (generation === workspaceDataGeneration) {
          setBoundedCacheEntry(detailCache, key, payload, DETAIL_CACHE_LIMIT);
        }
        return payload;
      })
      .finally(() => {
        // Только свой запрос: после инвалидации по этому ключу мог стартовать новый.
        if (detailRequestCache.get(key) === request) {
          detailRequestCache.delete(key);
        }
      });

    detailRequestCache.set(key, request);
    return request;
  }

  async function getChartPayload(url) {
    if (!url) {
      throw new Error("wash-chart-url-missing");
    }

    if (chartPayloadCache.has(url)) {
      return chartPayloadCache.get(url);
    }

    if (chartPayloadRequestCache.has(url)) {
      return chartPayloadRequestCache.get(url);
    }

    const generation = workspaceDataGeneration;
    const request = fetchWithTimeout(url)
      .then((response) => {
        if (!response.ok) {
          throw new Error("chart-data-request-failed");
        }
        return response.json();
      })
      .then((payload) => {
        if (generation === workspaceDataGeneration) {
          setBoundedCacheEntry(chartPayloadCache, url, payload, CHART_PAYLOAD_CACHE_LIMIT);
        }
        return payload;
      })
      .finally(() => {
        if (chartPayloadRequestCache.get(url) === request) {
          chartPayloadRequestCache.delete(url);
        }
      });

    chartPayloadRequestCache.set(url, request);
    return request;
  }

  function prefetchChartPayload(url) {
    if (!url) {
      return;
    }
    void getChartPayload(url).catch(() => {});
  }

  function prefetchWashContext(key) {
    if (!key) {
      return;
    }

    void getDetail(key)
      .then((detail) => {
        if (detail?.chart_data_url) {
          return getChartPayload(detail.chart_data_url);
        }
        return null;
      })
      .catch(() => {});
  }

  function scheduleSearchRender() {
    if (searchRenderTimer) {
      window.clearTimeout(searchRenderTimer);
    }

    searchRenderTimer = window.setTimeout(() => {
      searchRenderTimer = 0;
      renderWashList({ resetScroll: true });
    }, SEARCH_INPUT_DEBOUNCE_MS);
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

  async function persistObjectName(channel, objectId, name = "", mode = "set") {
    const response = await fetchWithTimeout("/api/object-name", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        channel,
        object_id: objectId,
        name,
        mode,
      }),
    });

    if (!response.ok) {
      let errorMessage = "Не удалось сохранить новое название объекта.";
      try {
        const payload = await response.json();
        if (payload?.detail) {
          errorMessage = String(payload.detail);
        }
      } catch (_error) {
        // Fall back to the generic message.
      }
      throw new Error(errorMessage);
    }

    return response.json();
  }

  function applyObjectNameToWashData(channel, objectId, objectName) {
    const rowIndexes = state.washRowIndexesByObjectKey.get(buildWashObjectKey(channel, objectId)) || [];
    rowIndexes.forEach((index) => {
      const row = state.washRows[index];
      if (!row) {
        return;
      }

      const updatedRow = {
        ...row,
        object: objectName,
      };
      updatedRow.search_blob = buildSearchBlob(updatedRow);
      state.washRows[index] = updatedRow;
    });

    detailCache.forEach((detail, key) => {
      if (Number(detail.channel) !== Number(channel) || Number(detail.object_id) !== Number(objectId)) {
        return;
      }
      detailCache.set(key, {
        ...detail,
        object_name: objectName,
      });
    });

    renderWashList({ resetScroll: false });
  }

  function buildPrintFileName(detail) {
    const safeParts = [detail.object_name, detail.program, detail.start_time || detail.date_time]
      .filter(Boolean)
      .map((value) =>
        String(value)
          .trim()
          .replaceAll(/[\\/:*?"<>|]+/g, "-")
          .replaceAll(/\s+/g, "_")
      );

    return safeParts.length ? safeParts.join("__") : `wash_cycle_${Date.now()}`;
  }

  // Число с одним знаком после запятой (концентрация/уставка), либо «—».
  function formatConcentration(value) {
    return value === null || value === undefined || !Number.isFinite(Number(value))
      ? "—"
      : `${Number(value).toFixed(1)} %`;
  }

  // Пары [подпись, значение] для оценённых фаз концентрации (unknown пропускаем).
  // Используется и в окне деталей, и в печатном отчёте — вид совпадает.
  // Датчик на возврате: оцениваем рабочий участок (полку). «Не достигла нормы» —
  // концентрация ни разу не вышла на уровень (показываем пик); «провал» — вышла,
  // но просела на рабочем участке; «в норме» — вышла и держалась (мин полки).
  function concentrationSummaryRows(detail) {
    const phases = Array.isArray(detail.concentration_eval) ? detail.concentration_eval : [];
    return phases
      .filter((phase) => phase && phase.status !== "unknown")
      .map((phase) => {
        const norm = `норма ${formatConcentration(phase.norm)}`;
        let value;
        if (phase.status === "low" && phase.reason === "not_reached") {
          value = `макс ${formatConcentration(phase.peak)} / ${norm} — не достигла нормы`;
        } else if (phase.status === "low") {
          value = `мин на режиме ${formatConcentration(phase.floor)} / ${norm} — провал ниже нормы`;
        } else {
          value = `мин на режиме ${formatConcentration(phase.floor)} / ${norm} — в норме`;
        }
        return [`Концентрация: ${phase.label}`, value];
      });
  }

  function renderPrintSummaryRows(detail) {
    return [
      ["Объект", detail.object_name],
      ["Программа мойки", detail.program],
      ["Начало мойки", formatModalDateTime(detail.start_time || detail.date_time)],
      ["Конец мойки", formatModalDateTime(detail.end_time)],
      ["Длительность мойки", detail.duration],
      ["Результат", detail.status],
      ...concentrationSummaryRows(detail),
    ]
      .map(
        ([label, value]) => `
          <tr${label === "Результат" ? " data-wash-result-row" : ""}>
            <th scope="row">${escapeHtml(label)}</th>
            <td>${escapeHtml(String(value ?? "").trim() || "—")}</td>
          </tr>
        `
      )
      .join("");
  }

  // Печатный отчёт повторяет вид окна просмотра графика мойки:
  // та же карточка-сводка (chart-modal-summary-card + chart-modal-table)
  // и та же рамка графика, только без кнопок и навигации.
  function renderPrintDocument(detail) {
    return `
      <article class="chart-print-document" aria-label="Отчет по мойке">
        <div class="chart-modal-summary-card chart-print-summary-card">
          <table class="chart-modal-table" aria-label="Параметры мойки">
            <tbody>
              ${renderPrintSummaryRows(detail)}
            </tbody>
          </table>
        </div>
        <section class="chart-print-chart" aria-label="График мойки">
          <div class="chart-host chart-host--modal chart-host--print" data-chart-host></div>
          <div class="technical-empty chart-empty" data-chart-empty hidden>
            Подготавливаю данные графика.
          </div>
        </section>
      </article>
    `;
  }

  function clearPrintMode() {
    document.body.classList.remove("modal-print-mode");
    if (restoreDocumentTitle) {
      document.title = restoreDocumentTitle;
      restoreDocumentTitle = null;
    }
  }

  function clearDetachedPrintDocument() {
    printRoot.hidden = true;
    printRoot.innerHTML = "";
  }

  function enterExportMode() {
    document.body.classList.add("modal-export-mode");
  }

  function clearExportMode() {
    document.body.classList.remove("modal-export-mode");
  }

  function waitForNextFrame() {
    return new Promise((resolve) => window.requestAnimationFrame(() => resolve()));
  }

  async function prepareDetachedPrintDocument(detail) {
    printRoot.hidden = false;
    printRoot.innerHTML = renderPrintDocument(detail);

    try {
      await mountChart(printRoot, detail, null);
    } catch (_error) {
      // The print document keeps its empty state if chart rendering fails.
    }

    await waitForNextFrame();
    await waitForNextFrame();
  }

  async function withExportMode(action) {
    const scrollX = window.scrollX;
    const scrollY = window.scrollY;
    enterExportMode();
    window.scrollTo(0, 0);
    await waitForNextFrame();
    await waitForNextFrame();

    try {
      return await action();
    } finally {
      clearExportMode();
      window.scrollTo(scrollX, scrollY);
    }
  }

  function syncOverlayState() {
    const hasVisibleOverlay =
      !modalRoot.hidden ||
      !objectEditorRoot.hidden ||
      !settingsRoot.hidden ||
      !diagnosticsRoot.hidden;
    document.body.classList.toggle("modal-open", hasVisibleOverlay);
  }

  async function fetchDiagnostics() {
    const response = await fetchWithTimeout("/api/diagnostics", { headers: { Accept: "application/json" } });
    if (!response.ok) {
      throw new Error("diagnostics-fetch-failed");
    }
    return response.json();
  }

  function closeDiagnostics() {
    // Новый токен: ответы запросов открытой панели уже не применятся.
    diagnosticsOpenId += 1;
    diagnosticsRoot.hidden = true;
    diagnosticsRoot.innerHTML = "";
    syncOverlayState();
  }


  function hasDesktopPdfApi() {
    return typeof window.pywebview?.api?.save_graph_pdf === "function";
  }

  async function saveGraphPdf(detail, button) {
    let cleanupDetachedDocument = true;
    const originalLabel = button?.textContent || "Сохранить как PDF";

    if (button) {
      button.disabled = true;
      button.textContent = "Сохраняю PDF...";
    }

    try {
      if (!hasDesktopPdfApi()) {
        cleanupDetachedDocument = false;
        await printReportDocument(detail, "pdf");
        return;
      }

      await prepareDetachedPrintDocument(detail);

      const response = await withExportMode(() =>
        window.pywebview.api.save_graph_pdf({
          file_name: `${buildPrintFileName(detail)}.pdf`,
        })
      );

      if (response?.unsupported) {
        cleanupDetachedDocument = false;
        await printReportDocument(detail, "pdf");
        return;
      }

      if (!response?.ok && !response?.cancelled) {
        throw new Error("desktop-pdf-save-failed");
      }
      if (response?.ok) {
        showToast("PDF сохранён", "success");
      } else if (response?.cancelled) {
        showToast("Сохранение PDF отменено", "info");
      }
    } catch (_error) {
      showToast("Не удалось сохранить PDF. Попробуйте ещё раз.", "error");
    } finally {
      if (cleanupDetachedDocument) {
        clearDetachedPrintDocument();
      }
      if (button) {
        button.disabled = false;
        button.textContent = originalLabel;
      }
    }
  }

  async function printReportDocument(detail, intent = "print") {
    await prepareDetachedPrintDocument(detail);

    const originalTitle = document.title;
    if (intent === "pdf" && detail) {
      document.title = `${buildPrintFileName(detail)}.pdf`;
    }
    restoreDocumentTitle = originalTitle;

    document.body.classList.add("modal-print-mode");
    await waitForNextFrame();
    await waitForNextFrame();
    try {
      window.print();
    } catch (error) {
      // WebView без поддержки печати: снимаем печатный режим сразу, иначе body
      // залипнет в печатной вёрстке (afterprint не сработает — диалог не открылся).
      clearPrintMode();
      clearDetachedPrintDocument();
      throw error;
    }
    window.setTimeout(() => {
      clearPrintMode();
      clearDetachedPrintDocument();
    }, 1000);
  }

  async function saveWashRowPdf(key, button) {
    // Блокируем кнопку ДО getDetail (реальный сетевой запрос): иначе двойной клик
    // до его резолва запускал бы два saveGraphPdf на общий printRoot, перетирая
    // печатный документ друг друга. saveGraphPdf блокирует кнопку только у себя —
    // уже после загрузки детали.
    if (button?.disabled) {
      return;
    }
    if (button) {
      button.disabled = true;
    }
    try {
      const detail = await getDetail(key);
      await saveGraphPdf(detail, button);
    } catch (_error) {
      showToast("Не удалось сохранить PDF. Попробуйте ещё раз.", "error");
    } finally {
      if (button) {
        button.disabled = false;
      }
    }
  }

  async function mountChart(root, detail, requestId = modalRequestId) {
    const hosts = Array.from(root.querySelectorAll("[data-chart-host]"));
    const emptyStates = Array.from(root.querySelectorAll("[data-chart-empty]"));

    const setEmptyState = (visible, message = "") => {
      emptyStates.forEach((emptyState) => {
        emptyState.hidden = !visible;
        if (message) {
          emptyState.textContent = message;
        }
      });
    };

    if (!hosts.length || !detail?.chart_data_url || !window.WashChart?.mount) {
      hosts.forEach((host) => {
        host.hidden = true;
      });
      setEmptyState(true);
      return false;
    }

    hosts.forEach((host) => {
      host.hidden = false;
    });
    setEmptyState(false);

    try {
      const [payload] = await Promise.all([
        getChartPayload(detail.chart_data_url),
        window.WashChart.hydrate ? window.WashChart.hydrate() : Promise.resolve(),
      ]);

      if (requestId !== null && requestId !== modalRequestId) {
        return false;
      }

      let renderedCount = 0;
      hosts.forEach((host) => {
        if (!host.isConnected) {
          return;
        }
        const rendered = window.WashChart.mount(host, payload);
        host.hidden = !rendered;
        if (rendered) {
          renderedCount += 1;
        }
      });

      if (!renderedCount) {
        setEmptyState(true, "Для этой мойки не найдено данных для построения графика.");
      }
      return renderedCount > 0;
    } catch (_error) {
      hosts.forEach((host) => {
        host.hidden = true;
      });
      setEmptyState(true, "Не удалось загрузить данные графика.");
      return false;
    }
  }

  function closeObjectEditor() {
    objectEditorRoot.hidden = true;
    objectEditorRoot.innerHTML = "";
    syncOverlayState();
  }

  function closeSettings() {
    // Новый токен: ответы запросов открытой панели уже не применятся.
    settingsOpenId += 1;
    if (archiveCleanupInterval !== null) {
      clearInterval(archiveCleanupInterval);
      archiveCleanupInterval = null;
    }
    settingsRoot.hidden = true;
    settingsRoot.innerHTML = "";
    syncOverlayState();
  }

  async function fetchAppSettings() {
    const response = await fetchWithTimeout("/api/settings", { headers: { Accept: "application/json" } });
    if (!response.ok) {
      throw new Error("settings-fetch-failed");
    }
    const payload = await response.json();
    return payload && typeof payload.settings === "object" ? payload.settings : {};
  }

  async function saveAppSettings(patch) {
    const response = await fetchWithTimeout("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ settings: patch }),
    });
    if (!response.ok) {
      throw new Error("settings-save-failed");
    }
    const payload = await response.json();
    return payload && typeof payload.settings === "object" ? payload.settings : patch;
  }

  async function fetchChartStyles() {
    const response = await fetchWithTimeout("/api/chart-styles", { headers: { Accept: "application/json" } });
    if (!response.ok) {
      throw new Error("chart-styles-fetch-failed");
    }
    const payload = await response.json();
    return {
      series: payload && typeof payload.series === "object" ? payload.series : {},
      defaults: Array.isArray(payload?.defaults) ? payload.defaults : [],
    };
  }

  async function saveChartStyles(series) {
    const response = await fetchWithTimeout("/api/chart-styles", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ series }),
    });
    if (!response.ok) {
      throw new Error("chart-styles-save-failed");
    }
    const payload = await response.json();
    return payload && typeof payload.series === "object" ? payload.series : series;
  }

  function renderChartStyleControls(defaults, series) {
    if (!defaults.length) {
      return '<div class="settings-chart-empty">Нет настраиваемых кривых.</div>';
    }
    return defaults
      .map((def) => {
        const saved = series[def.id] || {};
        const color = isValidHexColorLike(saved.color) ? saved.color : def.color;
        const lineStyle = CHART_LINE_STYLE_OPTIONS.some((option) => option.id === saved.lineStyle)
          ? saved.lineStyle
          : def.lineStyle;
        const options = CHART_LINE_STYLE_OPTIONS.map(
          (option) =>
            `<option value="${option.id}" ${option.id === lineStyle ? "selected" : ""}>${escapeHtml(option.label)}</option>`
        ).join("");
        return `
          <div class="settings-chart-row" style="--settings-chart-color: ${escapeHtml(color)};">
            <span class="settings-chart-swatch" aria-hidden="true"></span>
            <span class="settings-chart-name">${escapeHtml(def.label)}</span>
            <span class="wash-chart-color-input-shell">
              <input type="color" value="${escapeHtml(color)}" data-chart-style-color="${escapeHtml(def.id)}" aria-label="Цвет: ${escapeHtml(def.label)}">
            </span>
            <span class="wash-chart-select-shell">
              <select data-chart-style-line="${escapeHtml(def.id)}" aria-label="Тип линии: ${escapeHtml(def.label)}">${options}</select>
            </span>
          </div>
        `;
      })
      .join("");
  }

  function isValidHexColorLike(value) {
    return /^#[0-9a-f]{6}$/i.test(String(value || ""));
  }

  async function openSettings(initialPage = "general") {
    if (!modalRoot.hidden) {
      closeChartModal();
    }
    if (!objectEditorRoot.hidden) {
      closeObjectEditor();
    }
    const openId = ++settingsOpenId;
    settingsRoot.hidden = false;
    syncOverlayState();

    let settings = { ftp_auto_refresh_enabled: true, ftp_auto_refresh_minutes: 5, default_folder_path: "" };
    try {
      settings = { ...settings, ...(await fetchAppSettings()) };
    } catch (_error) {
      // Не удалось получить настройки — показываем значения по умолчанию.
    }
    if (openId !== settingsOpenId) {
      return;
    }

    let chartStyles = { series: {}, defaults: [] };
    try {
      chartStyles = await fetchChartStyles();
    } catch (_error) {
      // Стили графика недоступны — секцию покажем пустой.
    }

    // Пользователь мог закрыть окно (или открыть заново), пока грузились
    // настройки: старый ответ не должен перетирать свежую панель.
    if (openId !== settingsOpenId || settingsRoot.hidden) {
      return;
    }

    const resultLabels = settings.result_labels || {};
    settingsRoot.innerHTML = `
      <div class="object-editor-backdrop" data-close-settings></div>
      <section class="object-editor-panel object-editor-panel--settings" role="dialog" aria-modal="true" aria-label="Настройки">
        <header class="settings-header">
          <div class="settings-header-title">
            <span class="settings-header-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" focusable="false"><path d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58a.49.49 0 0 0 .12-.61l-1.92-3.32a.49.49 0 0 0-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54a.48.48 0 0 0-.48-.41h-3.84a.48.48 0 0 0-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96a.49.49 0 0 0-.59.22L2.74 8.87a.49.49 0 0 0 .12.61l2.03 1.58c-.05.3-.09.63-.09.94s.02.64.07.94l-2.03 1.58a.49.49 0 0 0-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6a3.6 3.6 0 1 1 0-7.2 3.6 3.6 0 0 1 0 7.2z"></path></svg>
            </span>
            <h2>Настройки</h2>
          </div>
          <button type="button" class="chart-modal-icon-button chart-modal-icon-button--danger" data-close-settings aria-label="Закрыть настройки" title="Закрыть">
            <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">
              <path d="M5 5L15 15"></path>
              <path d="M15 5L5 15"></path>
            </svg>
          </button>
        </header>
        <div class="settings-macos">
          <nav class="settings-nav" role="tablist" aria-label="Разделы настроек">
            ${SETTINGS_PAGES.map((page, index) => `
              <button type="button" class="settings-nav-item${index === 0 ? " is-active" : ""}" role="tab" data-settings-nav="${page.id}" aria-selected="${index === 0 ? "true" : "false"}">
                <span class="settings-nav-icon" aria-hidden="true">${page.icon}</span>
                <span class="settings-nav-label">${escapeHtml(page.label)}</span>
              </button>
            `).join("")}
          </nav>
          <div class="settings-content">
            <section class="settings-page" data-settings-page="general">
              <h3 class="settings-page-title">Общие</h3>

              <h4 class="settings-group-title">Результат мойки</h4>
              <label class="settings-option">
                <span class="settings-option-text"><strong>Показывать результат</strong></span>
                <input type="checkbox" data-setting-wash-result ${isWashResultVisible() ? "checked" : ""}>
              </label>
              ${RESULT_LABEL_FIELDS.map((field) => `
                <div class="settings-option settings-option--stacked">
                  <span class="settings-option-text"><strong>${escapeHtml(field.label)}</strong></span>
                  <input type="text" class="settings-text-input" data-setting-result-label="${field.key}" maxlength="120" placeholder="${escapeHtml(field.def)}" value="${escapeHtml(resultLabels[field.key] || field.def)}" autocomplete="off" spellcheck="false">
                </div>
              `).join("")}
              <p class="settings-note">Требовать финальный шаг «Окончание мойки»: если включено, мойка без него помечается «требует проверки». Многие станции этот шаг не пишут — по умолчанию выключено.</p>
              <label class="settings-option">
                <span class="settings-option-text"><strong>Требовать шаг окончания мойки</strong></span>
                <input type="checkbox" data-setting-require-completion ${settings.require_completion_step ? "checked" : ""}>
              </label>

              <h4 class="settings-group-title">Нормативы концентрации</h4>
              <p class="settings-note">Мин. концентрация рабочего раствора за фазу сравнивается с нормативом. Ниже нормы — мойка помечается «требует проверки». Нормативы вводятся вручную (проценты), пустое поле — фаза не оценивается.</p>
              <label class="settings-option">
                <span class="settings-option-text"><strong>Оценивать концентрацию</strong></span>
                <input type="checkbox" data-setting-concentration-enabled ${settings.concentration_eval_enabled ? "checked" : ""}>
              </label>
              <label class="settings-option">
                <span class="settings-option-text"><strong>Норматив щёлочи, %</strong></span>
                <input type="number" min="0" max="100" step="0.1" data-setting-concentration-norm="alkali" placeholder="—" value="${formatNormValue(settings.concentration_norms?.alkali)}">
              </label>
              <label class="settings-option">
                <span class="settings-option-text"><strong>Норматив кислоты, %</strong></span>
                <input type="number" min="0" max="100" step="0.1" data-setting-concentration-norm="acid" placeholder="—" value="${formatNormValue(settings.concentration_norms?.acid)}">
              </label>
              <label class="settings-option">
                <span class="settings-option-text"><strong>Допуск, % (0–100)</strong></span>
                <input type="number" min="0" max="100" step="1" data-setting-concentration-tolerance value="${Number(settings.concentration_tolerance_percent) || 0}">
              </label>

              <h4 class="settings-group-title">Источник данных</h4>
              <div class="settings-option settings-option--stacked">
                <span class="settings-option-text"><strong>Папка по умолчанию</strong></span>
                <input type="text" class="settings-text-input" data-setting-default-folder placeholder="Встроенная папка datalog" value="${escapeHtml(settings.default_folder_path || "")}" autocomplete="off" spellcheck="false">
              </div>
            </section>
            <section class="settings-page" data-settings-page="ftp" hidden>
              <h3 class="settings-page-title">FTP</h3>
              <label class="settings-option">
                <span class="settings-option-text"><strong>Автообновление включено</strong></span>
                <input type="checkbox" data-setting-ftp-auto-refresh ${settings.ftp_auto_refresh_enabled ? "checked" : ""}>
              </label>
              <label class="settings-option">
                <span class="settings-option-text"><strong>Интервал, мин</strong></span>
                <input type="number" min="1" max="1440" step="1" data-setting-ftp-auto-refresh-minutes value="${Number(settings.ftp_auto_refresh_minutes) || 5}">
              </label>
            </section>
            <section class="settings-page" data-settings-page="chart" hidden>
              <h3 class="settings-page-title">График</h3>
              <div class="settings-option settings-option--stacked">
                <span class="settings-option-text"><strong>Цвета и линии</strong></span>
                <div class="settings-chart-grid" data-chart-style-grid>${renderChartStyleControls(chartStyles.defaults, chartStyles.series)}</div>
                <div class="settings-chart-actions">
                  <button type="button" class="ghost" data-chart-style-reset>Сбросить</button>
                </div>
              </div>
              <div class="settings-option settings-option--stacked">
                <span class="settings-option-text"><strong>Кэш графиков</strong></span>
                <p class="settings-note">Сбрасывает сохранённые графики — они соберутся заново при следующем открытии. Помогает, если график отображается в устаревшем виде.</p>
                <div class="settings-chart-actions">
                  <button type="button" class="ghost" data-chart-cache-clear>Очистить кэш графиков</button>
                </div>
              </div>
            </section>
            <section class="settings-page" data-settings-page="updates" hidden>
              <h3 class="settings-page-title">Обновления и автозапуск</h3>
              <div class="settings-option">
                <span class="settings-option-text"><strong>Проверка обновлений</strong><span class="settings-option-hint">Сверяет текущую версию ${escapeHtml(appState.appVersion || "")} с последним релизом на GitHub</span></span>
                <button type="button" class="ghost" data-check-updates>Проверить</button>
              </div>
              <label class="settings-option">
                <span class="settings-option-text"><strong>Автозапуск с Windows</strong></span>
                <input type="checkbox" data-setting-autostart ${settings.autostart ? "checked" : ""}>
              </label>
              <!-- Панель обновления: заполняется renderUpdatePanel() по данным
                   /api/update-check. Пока обновления нет — остаётся пустой. -->
              <div class="update-panel" data-update-panel hidden></div>
            </section>
            <section class="settings-page" data-settings-page="archives" hidden>
              <h3 class="settings-page-title">Архивы</h3>
              <label class="settings-option">
                <span class="settings-option-text"><strong>Автоочистка архивов</strong></span>
                <input type="checkbox" data-setting-archive-retention ${settings.archive_retention_enabled ? "checked" : ""}>
              </label>
              <label class="settings-option">
                <span class="settings-option-text"><strong>Хранить, дней (1–730)</strong></span>
                <input type="number" min="1" max="730" step="1" data-setting-retention-days value="${Number(settings.archive_retention_days) || 365}">
              </label>
              <div class="settings-chart-actions">
                <button type="button" class="ghost" data-archive-cleanup-now>Очистить сейчас</button>
              </div>
              <div class="archive-confirm" data-archive-confirm hidden>
                <p class="settings-note" data-archive-confirm-text></p>
                <div class="settings-chart-actions">
                  <button type="button" class="ghost" data-archive-confirm-cancel>Отмена</button>
                  <button type="button" data-archive-confirm-ok disabled>Очистить</button>
                </div>
              </div>
            </section>
            <section class="settings-page" data-settings-page="diagnostics" hidden>
              <h3 class="settings-page-title">Диагностика</h3>
              <div class="diagnostics-body" data-diagnostics-body>
                <div class="technical-empty">Загрузка…</div>
              </div>
            </section>
          </div>
        </div>
      </section>
    `;
    settingsRoot.querySelectorAll("[data-close-settings]").forEach((element) => {
      element.addEventListener("click", closeSettings);
    });
    const washResultToggle = settingsRoot.querySelector("[data-setting-wash-result]");
    if (washResultToggle) {
      washResultToggle.addEventListener("change", (event) => {
        setWashResultVisible(event.currentTarget.checked);
      });
    }

    const autoRefreshToggle = settingsRoot.querySelector("[data-setting-ftp-auto-refresh]");
    const autoRefreshMinutes = settingsRoot.querySelector("[data-setting-ftp-auto-refresh-minutes]");

    const persistAutoRefresh = async () => {
      const minutesRaw = Number(autoRefreshMinutes?.value);
      const minutes = Number.isFinite(minutesRaw) ? Math.min(1440, Math.max(1, Math.round(minutesRaw))) : 5;
      try {
        const saved = await saveAppSettings({
          ftp_auto_refresh_enabled: Boolean(autoRefreshToggle?.checked),
          ftp_auto_refresh_minutes: minutes,
        });
        // Нормализованное сервером значение возвращаем в поле.
        if (autoRefreshMinutes && saved && Number.isFinite(Number(saved.ftp_auto_refresh_minutes))) {
          autoRefreshMinutes.value = Number(saved.ftp_auto_refresh_minutes);
        }
        showToast("Настройки сохранены", "success");
      } catch (_error) {
        showToast("Не удалось сохранить настройки.", "error");
      }
    };

    if (autoRefreshToggle) {
      autoRefreshToggle.addEventListener("change", persistAutoRefresh);
    }
    if (autoRefreshMinutes) {
      autoRefreshMinutes.addEventListener("change", persistAutoRefresh);
    }

    const defaultFolderInput = settingsRoot.querySelector("[data-setting-default-folder]");
    if (defaultFolderInput) {
      defaultFolderInput.addEventListener("change", async () => {
        // Через захваченную defaultFolderInput, а не event.currentTarget: он
        // обнуляется по завершении диспетчеризации, и после await обращение к
        // нему бросало бы TypeError → ложный тост «Не удалось сохранить».
        try {
          const saved = await saveAppSettings({
            default_folder_path: String(defaultFolderInput.value || "").trim(),
          });
          if (saved && typeof saved.default_folder_path === "string") {
            defaultFolderInput.value = saved.default_folder_path;
          }
          showToast("Настройки сохранены", "success");
        } catch (_error) {
          showToast("Не удалось сохранить настройки.", "error");
        }
      });
    }

    const chartStyleGrid = settingsRoot.querySelector("[data-chart-style-grid]");
    const chartStyleReset = settingsRoot.querySelector("[data-chart-style-reset]");

    let chartStyleDefaults = new Map(chartStyles.defaults.map((def) => [def.id, def]));

    // Шлём только отличия от дефолтов (как wash-chart.js при сохранении из
    // графика): иначе дефолты всех серий превращаются в вечные оверрайды на
    // сервере и смена палитры в SERIES_CONFIG перестаёт применяться.
    const collectChartStyleSeries = () => {
      const series = {};
      if (!chartStyleGrid) {
        return series;
      }
      chartStyleGrid.querySelectorAll("[data-chart-style-color]").forEach((input) => {
        const id = input.dataset.chartStyleColor;
        const def = chartStyleDefaults.get(id) || {};
        const lineSelect = chartStyleGrid.querySelector(`[data-chart-style-line="${id}"]`);
        const color = String(input.value || "");
        const lineStyle = lineSelect ? String(lineSelect.value || "") : "";
        const entry = {};

        if (
          isValidHexColorLike(color) &&
          color.toLowerCase() !== String(def.color || "").toLowerCase()
        ) {
          entry.color = color;
        }
        if (lineStyle && lineStyle !== String(def.lineStyle || "solid")) {
          entry.lineStyle = lineStyle;
        }
        if (Object.keys(entry).length) {
          series[id] = entry;
        }
      });
      return series;
    };

    const persistChartStyles = async () => {
      try {
        const saved = await saveChartStyles(collectChartStyleSeries());
        // Применяем к кэшу графиков, чтобы следующий открытый график сразу учёл изменения.
        window.WashChart?.setSeriesStyles?.(saved);
        showToast("Оформление графика сохранено", "success");
      } catch (_error) {
        showToast("Не удалось сохранить оформление графика.", "error");
      }
    };

    if (chartStyleGrid) {
      chartStyleGrid.addEventListener("change", (event) => {
        const target = event.target;
        if (!target?.dataset) {
          return;
        }
        if ("chartStyleColor" in target.dataset) {
          const row = target.closest(".settings-chart-row");
          if (row) {
            row.style.setProperty("--settings-chart-color", target.value);
          }
        }
        if ("chartStyleColor" in target.dataset || "chartStyleLine" in target.dataset) {
          void persistChartStyles();
        }
      });
    }

    if (chartStyleReset) {
      chartStyleReset.addEventListener("click", async () => {
        try {
          await saveChartStyles({});
          const fresh = await fetchChartStyles();
          if (openId !== settingsOpenId) {
            return;
          }
          chartStyleDefaults = new Map(fresh.defaults.map((def) => [def.id, def]));
          if (chartStyleGrid) {
            chartStyleGrid.innerHTML = renderChartStyleControls(fresh.defaults, fresh.series);
          }
          window.WashChart?.setSeriesStyles?.(fresh.series);
          showToast("Оформление графика сброшено", "success");
        } catch (_error) {
          if (openId !== settingsOpenId) {
            return;
          }
          showToast("Не удалось сбросить оформление графика.", "error");
        }
      });
    }

    const resultLabelInputs = settingsRoot.querySelectorAll("[data-setting-result-label]");
    const persistResultLabels = async () => {
      const labels = {};
      resultLabelInputs.forEach((input) => {
        labels[input.dataset.settingResultLabel] = String(input.value || "").trim();
      });
      try {
        await saveAppSettings({ result_labels: labels });
        // Обновляем список моек, чтобы новые подписи применились сразу.
        if (appState.hasWorkspace) {
          hydrateWorkspaceData({ resetScroll: false }).catch(() => {});
        }
        showToast("Настройки сохранены", "success");
      } catch (_error) {
        showToast("Не удалось сохранить настройки.", "error");
      }
    };
    resultLabelInputs.forEach((input) => {
      input.addEventListener("change", persistResultLabels);
    });

    const requireCompletionToggle = settingsRoot.querySelector("[data-setting-require-completion]");
    if (requireCompletionToggle) {
      requireCompletionToggle.addEventListener("change", async (event) => {
        try {
          await saveAppSettings({ require_completion_step: Boolean(event.currentTarget.checked) });
          // Пересчёт статуса мойки — на чтении, поэтому обновляем список сразу.
          if (appState.hasWorkspace) {
            hydrateWorkspaceData({ resetScroll: false }).catch(() => {});
          }
          showToast("Настройки сохранены", "success");
        } catch (_error) {
          showToast("Не удалось сохранить настройки.", "error");
        }
      });
    }

    // Проверка обновлений — явное действие: одна проверка на нажатие. Кнопку
    // блокируем на время запроса, иначе несколько кликов подряд наплодят
    // параллельных походов к GitHub.
    const checkUpdatesButton = settingsRoot.querySelector("[data-check-updates]");
    if (checkUpdatesButton) {
      checkUpdatesButton.addEventListener("click", async (event) => {
        const button = event.currentTarget;
        button.disabled = true;
        button.textContent = "Проверяю…";
        try {
          await checkForUpdates();
        } finally {
          button.disabled = false;
          button.textContent = "Проверить";
        }
      });
    }

    // Панель обновления перерисовывается целиком на каждом шаге, поэтому клик
    // по кнопке установки — делегированный.
    renderUpdatePanel();
    const updatePanel = settingsRoot.querySelector("[data-update-panel]");
    if (updatePanel) {
      updatePanel.addEventListener("click", (event) => {
        const button = event.target.closest("[data-update-install]");
        if (!button) {
          return;
        }
        button.disabled = true;
        runHandler(startUpdateInstall());
      });
    }

    const autostartToggle = settingsRoot.querySelector("[data-setting-autostart]");
    if (autostartToggle) {
      autostartToggle.addEventListener("change", async (event) => {
        const enabled = Boolean(event.currentTarget.checked);
        try {
          await saveAppSettings({ autostart: enabled });
          const api = window.pywebview && window.pywebview.api;
          if (api && typeof api.set_autostart === "function") {
            const result = await api.set_autostart({ enabled });
            if (result && result.supported === false) {
              showToast("Автозапуск доступен только в приложении Windows.", "info");
            } else if (result && result.ok) {
              showToast(enabled ? "Автозапуск включён" : "Автозапуск выключен", "success");
            } else {
              showToast("Не удалось изменить автозапуск.", "error");
            }
          } else {
            showToast("Автозапуск доступен только в приложении Windows.", "info");
          }
        } catch (_error) {
          showToast("Не удалось изменить автозапуск.", "error");
        }
      });
    }

    const retentionToggle = settingsRoot.querySelector("[data-setting-archive-retention]");
    const retentionDays = settingsRoot.querySelector("[data-setting-retention-days]");
    const persistRetention = async () => {
      const daysRaw = Number(retentionDays?.value);
      const days = Number.isFinite(daysRaw) ? Math.min(730, Math.max(1, Math.round(daysRaw))) : 365;
      try {
        const saved = await saveAppSettings({
          archive_retention_enabled: Boolean(retentionToggle?.checked),
          archive_retention_days: days,
        });
        if (retentionDays && saved && Number.isFinite(Number(saved.archive_retention_days))) {
          retentionDays.value = Number(saved.archive_retention_days);
        }
        showToast("Настройки сохранены", "success");
      } catch (_error) {
        showToast("Не удалось сохранить настройки.", "error");
      }
    };
    if (retentionToggle) {
      retentionToggle.addEventListener("change", persistRetention);
    }
    if (retentionDays) {
      retentionDays.addEventListener("change", persistRetention);
    }

    // Очистка архивов — необратимое удаление, поэтому подтверждение с обратным
    // отсчётом: кнопка «Очистить» разблокируется только через 30 секунд (защита
    // от случайного нажатия), «Отмена» доступна всегда.
    const cleanupButton = settingsRoot.querySelector("[data-archive-cleanup-now]");
    const cleanupConfirm = settingsRoot.querySelector("[data-archive-confirm]");
    const cleanupConfirmText = settingsRoot.querySelector("[data-archive-confirm-text]");
    const cleanupConfirmOk = settingsRoot.querySelector("[data-archive-confirm-ok]");
    const cleanupConfirmCancel = settingsRoot.querySelector("[data-archive-confirm-cancel]");
    const CLEANUP_DELAY_SECONDS = 30;

    const stopCleanupTimer = () => {
      if (archiveCleanupInterval !== null) {
        clearInterval(archiveCleanupInterval);
        archiveCleanupInterval = null;
      }
    };

    const hideCleanupConfirm = () => {
      stopCleanupTimer();
      if (cleanupConfirm) cleanupConfirm.hidden = true;
      if (cleanupButton) cleanupButton.hidden = false;
    };

    if (cleanupButton && cleanupConfirm && cleanupConfirmOk) {
      cleanupButton.addEventListener("click", () => {
        const days = Number(retentionDays?.value) || 365;
        if (cleanupConfirmText) {
          cleanupConfirmText.textContent =
            `Удалить архивы старше ${days} дней? Эти мойки исчезнут из журнала. ` +
            `Действие необратимо — кнопка «Очистить» станет доступна через ${CLEANUP_DELAY_SECONDS} с.`;
        }
        cleanupButton.hidden = true;
        cleanupConfirm.hidden = false;

        let remaining = CLEANUP_DELAY_SECONDS;
        cleanupConfirmOk.disabled = true;
        cleanupConfirmOk.textContent = `Очистить (${remaining})`;
        stopCleanupTimer();
        archiveCleanupInterval = setInterval(() => {
          remaining -= 1;
          if (remaining > 0) {
            cleanupConfirmOk.textContent = `Очистить (${remaining})`;
          } else {
            stopCleanupTimer();
            cleanupConfirmOk.disabled = false;
            cleanupConfirmOk.textContent = "Очистить";
          }
        }, 1000);
      });

      if (cleanupConfirmCancel) {
        cleanupConfirmCancel.addEventListener("click", hideCleanupConfirm);
      }

      cleanupConfirmOk.addEventListener("click", async () => {
        if (cleanupConfirmOk.disabled) {
          return;
        }
        stopCleanupTimer();
        cleanupConfirmOk.disabled = true;
        try {
          const response = await fetchWithTimeout("/api/archives/cleanup", { method: "POST" });
          if (!response.ok) {
            throw new Error("cleanup-failed");
          }
          const data = await response.json();
          showToast(
            `Удалено файлов: ${data.removed || 0}, освобождено ${formatBytes(data.freed_bytes || 0)}`,
            "success"
          );
        } catch (_error) {
          showToast("Не удалось выполнить очистку.", "error");
        } finally {
          hideCleanupConfirm();
        }
      });
    }

    // Нормативы концентрации: тумблер + два норматива + допуск. Пустое поле
    // норматива = не задан (шлём null). Обновляем список, чтобы вердикт применился.
    const concentrationEnabled = settingsRoot.querySelector("[data-setting-concentration-enabled]");
    const concentrationNorms = settingsRoot.querySelectorAll("[data-setting-concentration-norm]");
    const concentrationTolerance = settingsRoot.querySelector("[data-setting-concentration-tolerance]");
    const persistConcentration = async () => {
      const norms = {};
      concentrationNorms.forEach((input) => {
        const raw = String(input.value ?? "").trim();
        if (raw === "") {
          norms[input.dataset.settingConcentrationNorm] = null;
        } else {
          const num = Number(raw);
          norms[input.dataset.settingConcentrationNorm] = Number.isFinite(num)
            ? Math.min(100, Math.max(0, num))
            : null;
        }
      });
      const tolRaw = Number(concentrationTolerance?.value);
      const tolerance = Number.isFinite(tolRaw) ? Math.min(100, Math.max(0, tolRaw)) : 0;
      try {
        const saved = await saveAppSettings({
          concentration_eval_enabled: Boolean(concentrationEnabled?.checked),
          concentration_norms: norms,
          concentration_tolerance_percent: tolerance,
        });
        // Возвращаем нормализованные сервером значения в поля.
        if (saved && saved.concentration_norms) {
          concentrationNorms.forEach((input) => {
            input.value = formatNormValue(saved.concentration_norms[input.dataset.settingConcentrationNorm]);
          });
        }
        if (concentrationTolerance && saved && Number.isFinite(Number(saved.concentration_tolerance_percent))) {
          concentrationTolerance.value = Number(saved.concentration_tolerance_percent);
        }
        if (appState.hasWorkspace) {
          hydrateWorkspaceData({ resetScroll: false }).catch(() => {});
        }
      } catch (_error) {
        showToast("Не удалось сохранить настройки.", "error");
      }
    };
    if (concentrationEnabled) {
      concentrationEnabled.addEventListener("change", persistConcentration);
    }
    concentrationNorms.forEach((input) => input.addEventListener("change", persistConcentration));
    if (concentrationTolerance) {
      concentrationTolerance.addEventListener("change", persistConcentration);
    }

    // Кнопка очистки кэша графиков.
    const chartCacheClear = settingsRoot.querySelector("[data-chart-cache-clear]");
    if (chartCacheClear) {
      chartCacheClear.addEventListener("click", async () => {
        chartCacheClear.disabled = true;
        try {
          const response = await fetchWithTimeout("/api/chart-cache/clear", { method: "POST" });
          if (!response.ok) {
            throw new Error("clear-failed");
          }
          const data = await response.json();
          showToast(`Кэш графиков очищен (файлов: ${data.removed || 0}).`, "success");
        } catch (_error) {
          showToast("Не удалось очистить кэш графиков.", "error");
        } finally {
          chartCacheClear.disabled = false;
        }
      });
    }

    // Боковая навигация разделов (macOS-стиль): переключение активной панели.
    let diagnosticsLoaded = false;
    const showSettingsPage = (pageId) => {
      settingsRoot.querySelectorAll("[data-settings-nav]").forEach((button) => {
        const active = button.dataset.settingsNav === pageId;
        button.classList.toggle("is-active", active);
        button.setAttribute("aria-selected", active ? "true" : "false");
      });
      settingsRoot.querySelectorAll("[data-settings-page]").forEach((panel) => {
        panel.hidden = panel.dataset.settingsPage !== pageId;
      });
      if (pageId === "diagnostics" && !diagnosticsLoaded) {
        diagnosticsLoaded = true;
        void loadDiagnosticsInto(settingsRoot.querySelector("[data-diagnostics-body]"));
      }
    };
    settingsRoot.querySelectorAll("[data-settings-nav]").forEach((button) => {
      button.addEventListener("click", () => showSettingsPage(button.dataset.settingsNav));
    });
    if (SETTINGS_PAGES.some((page) => page.id === initialPage)) {
      showSettingsPage(initialPage);
    }
  }

  // Строки диагностики (снимок состояния источника) для раздела в настройках.
  async function loadDiagnosticsInto(container) {
    if (!container) {
      return;
    }
    let data = null;
    try {
      data = await fetchDiagnostics();
    } catch (_error) {
      container.innerHTML = '<div class="technical-empty">Диагностика недоступна.</div>';
      return;
    }
    const kindLabel = { ftp: "FTP", folder: "Папка", none: "—" };
    const counts = data?.counts || {};
    const rows = data
      ? [
          ["Источник", kindLabel[data.source_kind] || "—"],
          ["Путь", data.display_root || "—"],
          ["Последняя синхронизация", data.last_sync || "—"],
          ["Моек", counts.cycles ?? 0],
          ["Объектов", counts.objects ?? 0],
          ["Баз данных", counts.databases ?? 0],
          ["Архивов", counts.archives ?? 0],
          ["FTP-панелей", counts.ftp_sources ?? 0],
          [
            "Автообновление",
            data.auto_refresh?.enabled ? `вкл · ${data.auto_refresh.minutes} мин` : "выкл",
          ],
          ["Объём datalog", formatBytes(data.datalog?.size_bytes || 0)],
          [
            "Хранение архивов",
            data.datalog?.retention_enabled ? `${data.datalog.retention_days} дней` : "выкл",
          ],
          ["Последняя очистка", data.datalog?.last_cleanup || "—"],
          ["Обработка", data.job?.active ? data.job.message || "выполняется" : "нет"],
          ["Ошибка", data.error || "—"],
        ]
      : [["Диагностика", "недоступна"]];
    container.innerHTML = rows
      .map(
        ([key, value]) => `
          <div class="diagnostics-row">
            <span class="diagnostics-key">${escapeHtml(key)}</span>
            <span class="diagnostics-value">${escapeHtml(String(value))}</span>
          </div>
        `
      )
      .join("");
  }

  function formatBytes(bytes) {
    const value = Number(bytes) || 0;
    if (value < 1024) {
      return `${value} Б`;
    }
    const units = ["КБ", "МБ", "ГБ", "ТБ"];
    let size = value / 1024;
    let unitIndex = 0;
    while (size >= 1024 && unitIndex < units.length - 1) {
      size /= 1024;
      unitIndex += 1;
    }
    return `${size.toFixed(size >= 10 ? 0 : 1)} ${units[unitIndex]}`;
  }

  // Вызывается только кнопкой в настройках — одна проверка на нажатие.
  // Результат всегда озвучиваем: пользователь нажал и ждёт ответа.
  async function checkForUpdates() {
    try {
      const response = await fetchWithTimeout("/api/update-check", { headers: { Accept: "application/json" } });
      if (!response.ok) {
        throw new Error("update-check-failed");
      }
      const data = await response.json();
      state.updateInfo = data;
      renderUpdatePanel();
      if (data.update_available) {
        // Куда вести — зависит от того, умеем ли ставить сами: в десктопе это
        // кнопка в настройках, в браузере остаётся страница релизов.
        const where = canInstallUpdate()
          ? "Установить можно ниже."
          : "Смотрите GitHub Releases.";
        showToast(`Доступно обновление ${data.latest}. ${where}`, "info", 8000);
      } else if (data.latest) {
        showToast("Установлена последняя версия.", "success");
      } else {
        // Пустой latest — это «не выяснили» (нет сети/релизов), а не «актуально»:
        // бэкенд глушит ошибки запроса к GitHub и отдаёт "". Врать про
        // актуальность нельзя — пользователь пропустит важное обновление.
        showToast("Не удалось проверить обновления.", "info");
      }
    } catch (_error) {
      showToast("Не удалось проверить обновления.", "error");
    }
  }

  // ---- Установка обновления в один клик ----------------------------------
  // Доступна только в десктоп-сборке под Windows: ставит .exe-установщик, а
  // закрыть окно и перезапуститься умеет лишь мост pywebview. В браузере
  // (и на не-Windows) панель показывает обычную ссылку на Releases.
  function canInstallUpdate() {
    const info = state.updateInfo;
    return Boolean(
      info &&
        info.installable &&
        typeof window.pywebview?.api?.install_update === "function",
    );
  }

  function renderUpdatePanel() {
    const panel = document.querySelector("[data-update-panel]");
    if (!panel) {
      return;
    }
    const info = state.updateInfo;
    if (!info || !info.update_available) {
      panel.hidden = true;
      panel.innerHTML = "";
      return;
    }

    panel.hidden = false;
    const job = state.updateJob;
    const version = escapeHtml(String(info.latest || ""));

    if (job && job.status === "running") {
      const pct = job.total ? Math.round((job.downloaded / job.total) * 100) : 0;
      const label =
        job.phase === "verify"
          ? "Проверка контрольной суммы…"
          : `Скачивание ${formatBytes(job.downloaded)} из ${formatBytes(job.total)}`;
      panel.innerHTML = `
        <div class="update-panel-title">Обновление ${version}</div>
        <div class="update-progress"><div class="update-progress-bar" style="width: ${pct}%"></div></div>
        <p class="settings-note">${escapeHtml(label)}</p>
      `;
      return;
    }

    if (job && job.status === "error") {
      panel.innerHTML = `
        <div class="update-panel-title">Обновление ${version}</div>
        <p class="settings-note update-panel-error">${escapeHtml(job.error || "Не удалось скачать обновление.")}</p>
        <div class="settings-chart-actions">
          <button type="button" class="ghost" data-update-install>Повторить</button>
        </div>
      `;
      return;
    }

    if (canInstallUpdate()) {
      panel.innerHTML = `
        <div class="update-panel-title">Доступно обновление ${version}</div>
        <p class="settings-note">Приложение скачает установщик, проверит его и закроется — Windows запросит подтверждение. После установки окно откроется снова.</p>
        <div class="settings-chart-actions">
          <button type="button" data-update-install>Установить обновление</button>
        </div>
      `;
      return;
    }

    panel.innerHTML = `
      <div class="update-panel-title">Доступно обновление ${version}</div>
      <p class="settings-note">Скачайте установщик со страницы релизов: <a href="${escapeHtml(info.url || "")}" target="_blank" rel="noopener noreferrer">GitHub Releases</a>.</p>
    `;
  }

  async function pollUpdateJob() {
    const response = await fetchWithTimeout("/api/update/job", { headers: { Accept: "application/json" } });
    if (!response.ok) {
      throw new Error("update-job-failed");
    }
    state.updateJob = await response.json();
    renderUpdatePanel();
    return state.updateJob;
  }

  async function startUpdateInstall() {
    // Повторный вход запрещён: бэкенд отбивает второй POST только пока job в
    // "running", а после "ready" — качает установщик заново и поднимает второй
    // воркер. Кнопка от этого не защищает: renderUpdatePanel на каждом опросе
    // вставляет свежую, уже включённую.
    if (state.updateBusy) {
      return;
    }
    state.updateBusy = true;
    try {
      const response = await fetchWithTimeout("/api/update/download", { method: "POST" });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || "Не удалось начать скачивание.");
      }
      state.updateJob = (await response.json()).job;
      renderUpdatePanel();

      // Опрос до завершения. Таймер снимаем на beforeunload (см. ниже) —
      // иначе он тикал бы уже после закрытия окна.
      let ticks = 0;
      while (true) {
        await new Promise((resolve) => {
          state.updateTimer = window.setTimeout(resolve, 500);
        });
        const job = await pollUpdateJob();
        if (!job || job.status !== "running") {
          break;
        }
        ticks += 1;
        // Потолок ≈20 минут (500 мс × 2400). Установщик — десятки мегабайт, на
        // тонком канале скачивание идёт долго, но залипший в "running" бэкенд
        // иначе держал бы 2 запроса в секунду вечно, даже при закрытых
        // настройках: цикл живёт в промисе, а не в панели.
        if (ticks >= UPDATE_POLL_MAX_TICKS) {
          throw new Error("Скачивание не завершилось за отведённое время.");
        }
      }

      const job = state.updateJob;
      if (!job || job.status !== "ready") {
        throw new Error(job?.error || "Не удалось скачать обновление.");
      }

      const result = await window.pywebview.api.install_update();
      if (!result?.ok) {
        // Отказ от UAC приходит сюда: приложение осталось живым, обновление не
        // установлено. Панель обязана показать причину и дать повтор.
        throw new Error(result?.error || "Не удалось запустить установщик.");
      }
      showToast("Запускаю установку — приложение закроется.", "info", 8000);
    } catch (error) {
      // Любой сбой (таймаут fetch, обрыв, не-200, отказ от UAC) обязан
      // перевести панель в "error": ветка "running" рисует прогресс-бар БЕЗ
      // кнопок, и панель залипала бы в «Скачивание…» до перезагрузки окна.
      const message = String(error.message || error);
      state.updateJob = { status: "error", error: message };
      renderUpdatePanel();
      showToast(message, "error", 8000);
    } finally {
      state.updateBusy = false;
    }
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
                <span class="object-editor-token">Канал ${escapeHtml(row.channel)}</span>
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

    if (channel < 1 || channel > 5) {
      return {
        blocked: true,
        severity: "error",
        message: "Выбери канал от 1 до 5.",
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
        message: `Для канала ${channel} и object id ${objectId} уже есть запись «${existingRow.object_name}».`,
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
          `Такое название уже используется у канала ${duplicateNameRow.channel}, object id ${duplicateNameRow.object_id}. ` +
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

  function openObjectEditor() {
    if (!modalRoot.hidden) {
      closeChartModal();
    }

    objectEditorRoot.hidden = false;
    syncOverlayState();
    objectEditorRoot.innerHTML = `
      <div class="object-editor-backdrop" data-close-object-editor></div>
      <section class="object-editor-panel" role="dialog" aria-modal="true" aria-label="Редактор названий объектов">
        <header class="object-editor-header">
          <div>
            <h2>Редактор объектов</h2>
            <p class="object-editor-copy">Названия объектов по каналам.</p>
          </div>
          <div class="object-editor-header-actions">
            <button type="button" class="chart-modal-icon-button chart-modal-icon-button--danger" data-close-object-editor aria-label="Закрыть редактор объектов" title="Закрыть">
              <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">
                <path d="M5 5L15 15"></path>
                <path d="M15 5L5 15"></path>
              </svg>
            </button>
          </div>
        </header>
        <div class="object-editor-list" id="objectEditorList">${renderObjectEditorRows()}</div>
        <footer class="object-editor-footer">
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
                <span>Канал</span>
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
    const addDialog = objectEditorRoot.querySelector("[data-object-editor-create]");
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

  function closeChartModal() {
    modalRequestId += 1;
    modalRoot.hidden = true;
    modalRoot.innerHTML = "";
    clearPrintMode();
    clearDetachedPrintDocument();
    syncOverlayState();
  }

  function renderModalLoading() {
    modalRoot.hidden = false;
    syncOverlayState();
    modalRoot.innerHTML = `
      <div class="chart-modal-backdrop" data-close-chart-modal></div>
      <section class="chart-modal-panel" role="dialog" aria-modal="true" aria-label="Загрузка графика">
        <header class="chart-modal-header">
          <div class="detail-title">Загрузка графика</div>
          <button
            type="button"
            class="chart-modal-icon-button chart-modal-icon-button--danger"
            data-close-chart-modal
            aria-label="Закрыть окно графика"
            title="Закрыть"
          >
            <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">
              <path d="M5 5L15 15"></path>
              <path d="M15 5L5 15"></path>
            </svg>
          </button>
        </header>
        <div class="chart-modal-skeleton" role="status" aria-label="Загрузка графика">
          <div class="chart-modal-skeleton-summary">
            <span class="skeleton skeleton-line" style="width:38%"></span>
            <span class="skeleton skeleton-line" style="width:58%"></span>
            <span class="skeleton skeleton-line" style="width:30%"></span>
          </div>
          <div class="skeleton chart-modal-skeleton-chart"></div>
        </div>
      </section>
    `;

    modalRoot.querySelectorAll("[data-close-chart-modal]").forEach((element) => {
      element.addEventListener("click", closeChartModal);
    });
  }

  async function openWashModal(key) {
    const requestId = ++modalRequestId;
    renderModalLoading();

    try {
      const detail = await getDetail(key);
      const navigation = getModalNavigation(key);
      prefetchChartPayload(detail.chart_data_url);
      prefetchWashContext(navigation.previous?.key || "");
      prefetchWashContext(navigation.next?.key || "");
      // Уточняем реальное состояние окна, чтобы сводка правильно показывалась/
      // скрывалась независимо от того, как окно развернули.
      await refreshWindowMaximizedState();
      if (requestId !== modalRequestId) {
        return;
      }

      modalRoot.hidden = false;
      syncOverlayState();
      modalRoot.innerHTML = `
        <div class="chart-modal-backdrop" data-close-chart-modal></div>
        <section class="chart-modal-panel" role="dialog" aria-modal="true" aria-label="Полноэкранный график мойки">
          <header class="chart-modal-header chart-modal-header--table">
            <div class="chart-modal-actions chart-modal-actions--top">
              <button type="button" class="chart-modal-button chart-modal-button--primary" data-download-pdf>Сохранить как PDF</button>
              <button type="button" class="chart-modal-button chart-modal-button--secondary" data-print-wash>Печать</button>
              <button
                type="button"
                class="chart-modal-icon-button chart-modal-icon-button--danger"
                data-close-chart-modal
                aria-label="Закрыть окно графика"
                title="Закрыть"
              >
                <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">
                  <path d="M5 5L15 15"></path>
                  <path d="M15 5L5 15"></path>
                </svg>
              </button>
            </div>
            <div class="chart-modal-summary-card">
              <table class="chart-modal-table" aria-label="Параметры мойки">
                <tbody>
                  <tr>
                    <th scope="row">Объект</th>
                    <td data-object-name-label>${escapeHtml(detail.object_name)}</td>
                  </tr>
                  <tr>
                    <th scope="row">Программа мойки</th>
                    <td>${escapeHtml(detail.program)}</td>
                  </tr>
                  <tr>
                    <th scope="row">Начало мойки</th>
                    <td>${escapeHtml(formatModalDateTime(detail.start_time || detail.date_time))}</td>
                  </tr>
                  <tr>
                    <th scope="row">Конец мойки</th>
                    <td>${escapeHtml(formatModalDateTime(detail.end_time))}</td>
                  </tr>
                  <tr>
                    <th scope="row">Длительность мойки</th>
                    <td>${escapeHtml(detail.duration)}</td>
                  </tr>
                  <tr data-wash-result-row>
                    <th scope="row">Результат</th>
                    <td><span class="${badgeClass(detail.status, detail.result_kind)}">${escapeHtml(detail.status)}</span></td>
                  </tr>
                  ${concentrationSummaryRows(detail)
                    .map(
                      ([label, value]) => `
                  <tr>
                    <th scope="row">${escapeHtml(label)}</th>
                    <td>${escapeHtml(value)}</td>
                  </tr>`
                    )
                    .join("")}
                </tbody>
              </table>
            </div>
          </header>
          <div class="chart-modal-frame">
            <div class="chart-modal-mini-title" aria-hidden="true">
              <span class="chart-modal-mini-object">${escapeHtml(detail.object_name)}</span>
              <span class="chart-modal-mini-wash">${escapeHtml(detail.program)}</span>
            </div>
            <div class="chart-host chart-host--modal" data-chart-host></div>
            <div class="technical-empty chart-empty" data-chart-empty hidden>
              Подготавливаю данные графика.
            </div>
          </div>
          <footer class="chart-modal-nav">
            <button
              type="button"
              class="chart-modal-nav-button chart-modal-nav-button--prev"
              data-open-wash-key="${navigation.previous ? escapeHtml(navigation.previous.key) : ""}"
              ${navigation.previous ? "" : "disabled"}
            >
              <span class="chart-modal-nav-icon" aria-hidden="true">←</span>
              <span class="chart-modal-nav-button-label">Предыдущая мойка</span>
            </button>
            <button
              type="button"
              class="chart-modal-nav-button chart-modal-nav-button--next"
              data-open-wash-key="${navigation.next ? escapeHtml(navigation.next.key) : ""}"
              ${navigation.next ? "" : "disabled"}
            >
              <span class="chart-modal-nav-icon" aria-hidden="true">→</span>
              <span class="chart-modal-nav-button-label">Следующая мойка</span>
            </button>
          </footer>
        </section>
      `;

      modalRoot.querySelectorAll("[data-close-chart-modal]").forEach((element) => {
        element.addEventListener("click", closeChartModal);
      });
      const pdfButton = modalRoot.querySelector("[data-download-pdf]");
      if (pdfButton) {
        pdfButton.addEventListener("click", () => {
          void saveGraphPdf(detail, pdfButton);
        });
      }
      const printButton = modalRoot.querySelector("[data-print-wash]");
      if (printButton) {
        printButton.addEventListener("click", () => {
          // runHandler, а не void: printReportDocument может отклониться (WebView
          // без печати) — иначе улетело бы в unhandled rejection.
          runHandler(printReportDocument(detail, "print"));
        });
      }
      modalRoot.querySelectorAll("[data-open-wash-key]").forEach((element) => {
        if (!element.dataset.openWashKey) {
          return;
        }
        element.addEventListener("click", () => runHandler(openWashModal(element.dataset.openWashKey)));
      });

      void mountChart(modalRoot, detail, requestId);
    } catch (_error) {
      if (requestId !== modalRequestId) {
        return;
      }

      modalRoot.hidden = false;
      syncOverlayState();
      modalRoot.innerHTML = `
        <div class="chart-modal-backdrop" data-close-chart-modal></div>
        <section class="chart-modal-panel" role="dialog" aria-modal="true" aria-label="Ошибка загрузки графика">
          <header class="chart-modal-header">
            <div class="detail-title">Не удалось открыть график</div>
            <button
              type="button"
              class="chart-modal-icon-button chart-modal-icon-button--danger"
              data-close-chart-modal
              aria-label="Закрыть окно графика"
              title="Закрыть"
            >
              <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">
                <path d="M5 5L15 15"></path>
                <path d="M15 5L5 15"></path>
              </svg>
            </button>
          </header>
          <div class="technical-empty">Попробуй выбрать мойку ещё раз.</div>
        </section>
      `;

      modalRoot.querySelectorAll("[data-close-chart-modal]").forEach((element) => {
        element.addEventListener("click", closeChartModal);
      });
    }
  }

  // ---- Клавиатурная навигация ------------------------------------------------
  function isTypingTarget(element) {
    if (!element) {
      return false;
    }
    const tag = element.tagName;
    return (
      tag === "INPUT" ||
      tag === "TEXTAREA" ||
      tag === "SELECT" ||
      element.isContentEditable === true
    );
  }

  function isAnyOverlayOpen() {
    return [modalRoot, printRoot, objectEditorRoot, settingsRoot, diagnosticsRoot].some(
      (root) => root && !root.hidden
    );
  }

  function focusWashRowByKey(key) {
    if (!key) {
      return false;
    }
    const escaped = typeof CSS !== "undefined" && CSS.escape ? CSS.escape(key) : key;
    const element = washList.querySelector(`.wash-row[data-key="${escaped}"]`);
    if (!element) {
      return false;
    }
    element.focus({ preventScroll: true });
    element.scrollIntoView({ block: "nearest" });
    return true;
  }

  // Стрелочная навигация по списку с учётом виртуализации: если целевая строка
  // вне отрисованного окна, сначала подвигаем scrollTop и синхронно
  // перерисовываем окно, затем ставим фокус.
  function moveWashSelection(delta, { fromEdge = false } = {}) {
    const items = state.displayItems;
    if (!items || !items.length) {
      return false;
    }
    const rowIndices = [];
    for (let i = 0; i < items.length; i += 1) {
      if (items[i].type === "row") {
        rowIndices.push(i);
      }
    }
    if (!rowIndices.length) {
      return false;
    }

    const active = document.activeElement;
    const currentKey =
      active && washList.contains(active)
        ? active.closest("[data-key]")?.dataset.key || ""
        : "";
    let pos = currentKey
      ? rowIndices.findIndex((index) => items[index].row.key === currentKey)
      : -1;

    let nextPos;
    if (pos === -1 || fromEdge) {
      nextPos = delta > 0 ? 0 : rowIndices.length - 1;
    } else {
      nextPos = Math.min(rowIndices.length - 1, Math.max(0, pos + delta));
    }

    const targetIndex = rowIndices[nextPos];
    const targetKey = items[targetIndex].row.key;

    if (isWashListVirtualized()) {
      const offsets = state.displayOffsets;
      const rowHeight = washListMetrics.rowHeight;
      const viewport = washList.clientHeight;
      const top = offsets[targetIndex] || 0;
      if (top < washList.scrollTop) {
        washList.scrollTop = top;
      } else if (top + rowHeight > washList.scrollTop + viewport) {
        washList.scrollTop = top + rowHeight - viewport;
      }
      renderVirtualizedWashList();
    }

    return focusWashRowByKey(targetKey);
  }

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (!diagnosticsRoot.hidden) {
        closeDiagnostics();
        return;
      }
      if (!settingsRoot.hidden) {
        closeSettings();
        return;
      }
      if (!objectEditorRoot.hidden) {
        closeObjectEditor();
        return;
      }
      if (!modalRoot.hidden) {
        closeChartModal();
      }
      return;
    }

    // «/» — быстрый фокус в поиск (когда не печатаем в поле и нет открытых окон).
    if (
      event.key === "/" &&
      !event.ctrlKey &&
      !event.metaKey &&
      !event.altKey &&
      !isTypingTarget(event.target) &&
      !isAnyOverlayOpen() &&
      searchInput
    ) {
      event.preventDefault();
      searchInput.focus();
      searchInput.select();
    }
  });
  window.addEventListener("afterprint", () => {
    clearPrintMode();
    clearDetachedPrintDocument();
  });
  washList.addEventListener("scroll", scheduleVirtualizedWashList);
  washList.addEventListener("click", (event) => {
    const pdfButton = event.target.closest("[data-download-row-pdf]");
    if (pdfButton && washList.contains(pdfButton)) {
      const row = pdfButton.closest("[data-key]");
      if (!row?.dataset.key) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      void saveWashRowPdf(row.dataset.key, pdfButton);
      return;
    }

    const row = event.target.closest("[data-key]");
    if (!row || !washList.contains(row) || !row.dataset.key) {
      return;
    }
    runHandler(openWashModal(row.dataset.key));
  });
  washList.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      moveWashSelection(event.key === "ArrowDown" ? 1 : -1);
      return;
    }
    if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      moveWashSelection(event.key === "Home" ? 1 : -1, { fromEdge: true });
      return;
    }
    if (event.key !== "Enter" && event.key !== " ") {
      return;
    }
    if (event.target.closest("[data-download-row-pdf]")) {
      return;
    }
    const row = event.target.closest("[data-key]");
    if (!row || !washList.contains(row) || !row.dataset.key) {
      return;
    }
    event.preventDefault();
    runHandler(openWashModal(row.dataset.key));
  });
  // Смена ширины окна может изменить высоту строки (медиа-запросы) и режим
  // виртуализации — кэш отрисованного окна сбрасываем, чтобы высоты перемерились.
  // Дебаунс: во время перетаскивания рамки resize сыплется десятками в секунду,
  // а нам достаточно перемерить строки один раз, когда размер устоялся.
  // Опрос скачивания обновления переживал бы закрытие окна: снимаем таймер.
  window.addEventListener("beforeunload", () => {
    if (state.updateTimer) {
      window.clearTimeout(state.updateTimer);
      state.updateTimer = null;
    }
  });

  let resizeDebounceTimer = 0;
  window.addEventListener("resize", () => {
    window.clearTimeout(resizeDebounceTimer);
    resizeDebounceTimer = window.setTimeout(() => {
      invalidateRenderedWashWindow();
      scheduleVirtualizedWashList();
    }, 120);
  });

  if (fluidWashListQuery) {
    const handleFluidLayoutChange = () => {
      invalidateRenderedWashWindow();
      scheduleVirtualizedWashList();
    };
    if (typeof fluidWashListQuery.addEventListener === "function") {
      fluidWashListQuery.addEventListener("change", handleFluidLayoutChange);
    } else if (typeof fluidWashListQuery.addListener === "function") {
      // Safari < 14.
      fluidWashListQuery.addListener(handleFluidLayoutChange);
    }
  }

  document.querySelectorAll("[data-sort-value]").forEach((button) => {
    button.addEventListener("click", () => {
      const nextValue = String(button.dataset.sortValue || "");
      if (!nextValue || sortOrder.value === nextValue) {
        return;
      }
      sortOrder.value = nextValue;
      syncSortButtons();
      renderWashList({ resetScroll: true });
    });
  });

  // channelFilter и sortOrder — скрытые input'ы (значение меняют кнопки тулбара,
  // которые сами вызывают renderWashList): input/change они не эмитят, слушатели
  // на них никогда не срабатывали.
  // ↓ из поиска переводит фокус на первую строку списка.
  searchInput.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      moveWashSelection(1, { fromEdge: true });
    }
  });
  searchInput.addEventListener("input", scheduleSearchRender);
  searchInput.addEventListener("change", () => {
    // Отменяем отложенный дебаунс-рендер, чтобы не рисовать список дважды.
    if (searchRenderTimer) {
      window.clearTimeout(searchRenderTimer);
      searchRenderTimer = 0;
    }
    renderWashList({ resetScroll: true });
  });

  function handleDayFilterChange() {
    state.activePeriodPreset = "";
    syncPeriodPresetButtons();
    syncDateFilterBounds();
    syncDayFilterButton();
    renderWashList({ resetScroll: true });
  }

  // Только change: input у <input type="date"> эмитится вместе с change при
  // выборе даты в календаре — список фильтровался и рисовался дважды.
  dayFilter.addEventListener("change", handleDayFilterChange);

  clearDateFiltersButton.addEventListener("click", () => {
    resetAllSearchFilters();
    renderWashList({ resetScroll: true });
  });

  if (workspaceRefreshForm) {
    workspaceRefreshForm.addEventListener("submit", async (event) => {
      event.preventDefault();

      const submitButton = workspaceRefreshForm.querySelector('button[type="submit"]');
      const originalLabel = submitButton?.textContent || "";
      if (submitButton) {
        submitButton.disabled = true;
        submitButton.textContent = "Обновляю...";
      }

      setScreenError("");

      try {
        const payload = await startWorkspaceRefresh();
        // Подключаем мониторинг только после подтверждённого старта новой задачи:
        // иначе первое сообщение потока — терминальный статус ПРЕДЫДУЩЕЙ задачи,
        // поток закрывается, и новая задача остаётся без наблюдения.
        workspaceJobFeed?.ensureMonitoring?.();
        if (payload?.job) {
          updateWorkspaceJobUi(payload.job, { keepVisible: true });
        }
      } catch (error) {
        setScreenError(error instanceof Error ? error.message : "Не удалось запустить обновление.");
      } finally {
        if (submitButton) {
          submitButton.disabled = false;
          submitButton.textContent = originalLabel;
        }
      }
    });
  }

  if (openObjectEditorButton) {
    openObjectEditorButton.disabled = true;
    openObjectEditorButton.addEventListener("click", () => openObjectEditor());
  }

  const openSettingsButton = document.querySelector("#openSettings");
  if (openSettingsButton) {
    openSettingsButton.addEventListener("click", () => runHandler(openSettings()));
  }

  initClock();
  // Автопроверки при старте нет: обновления проверяются только по кнопке в
  // настройках — одна проверка на нажатие, без походов к GitHub за спиной.
  applyWashResultVisibility();

  periodPresetButtons.forEach((button) => {
    button.addEventListener("click", () => {
      state.activePeriodPreset = button.dataset.periodPreset || DEFAULT_PERIOD_PRESET;
      dayFilter.value = "";
      syncDateFilterBounds();
      syncDayFilterButton();
      syncPeriodPresetButtons();
      renderWashList({ resetScroll: true });
    });
  });

  syncSortButtons();
  applyWorkspaceMeta({
    display_root: appState.displayRoot,
    summary: appState.summary,
    error: appState.error,
  });
  syncDayFilterButton();
  syncPeriodPresetButtons();
  setWashListSkeleton();
  void hydrateWorkspaceData({ resetScroll: true }).catch(() => {
    setScreenError("Не удалось загрузить список моек.");
    setWashListMessage("Не удалось загрузить список моек.");
    if (openObjectEditorButton) {
      openObjectEditorButton.disabled = false;
    }
  });
})();
