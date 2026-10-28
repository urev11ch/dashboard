// Стартовый экран: поиск панелей Weintek в локальных подсетях.

import { fetchWithTimeout } from "../core/api.js";
import { openFormDialog } from "../core/dialogs.js";
import { currentHomeMode } from "./home.js";

// Кнопка «Найти панель в сети»: скан локальной подсети по FTP (POST
// /api/ftp/discover), список найденных хостов, клик подставляет адрес в форму.
export function initFtpDiscovery() {
  const button = document.querySelector("[data-ftp-discover]");
  if (!button) {
    return;
  }
  // Кнопка живёт в шапке секции, а статус и результаты — ниже списка панелей,
  // поэтому корень ищем по документу, а не через closest от кнопки.
  const root = document.querySelector("[data-ftp-discover-root]");
  const statusEl = root?.querySelector("[data-ftp-discover-status]");
  const resultsEl = root?.querySelector("[data-ftp-discover-results]");
  // Поле подсети: обычно скрыто (сканируются подсети всех интерфейсов ПК);
  // раскрывается, когда скан ничего не нашёл — панель может стоять за
  // маршрутизатором, где своего адреса у ПК нет.
  const subnetInput = root?.querySelector("[data-ftp-subnet]");
  if (!root || !resultsEl) {
    return;
  }

  // Ручное подключение доступно всегда, а не только после неудачного скана:
  // панель за маршрутизатором скан не найдёт никогда, и прятать до него
  // единственный рабочий путь — значит прятать его навсегда.
  // Кнопка работает спойлером — и раскрывает форму, и сворачивает обратно:
  // раньше она умела только открывать, и убрать поля с экрана было нечем.
  const manualBtn = document.querySelector("[data-ftp-manual]");
  const manualPanel = document.querySelector("[data-ftp-add]");
  if (manualBtn && manualPanel) {
    manualBtn.addEventListener("click", () => {
      const willOpen = manualPanel.hidden;
      manualPanel.hidden = !willOpen;
      manualBtn.setAttribute("aria-expanded", willOpen ? "true" : "false");
      manualBtn.classList.toggle("is-open", willOpen);
      if (willOpen) {
        manualPanel.querySelector('[name="host"]')?.focus();
      }
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
  // Панель СОХРАНЯЕТСЯ (POST /workspace/ftp-source/add) и появляется карточкой
  // на главной; журнал открывается её кнопкой «Открыть журнал».
  const openConnectDialog = (panel) => {
    const displayName = panelDisplayName(panel);
    openFormDialog({
      action: "/workspace/ftp-source/add",
      // Имя — недоверенные данные из сети: openFormDialog пишет его textContent.
      title: displayName,
      hidden: [
        ["host", panel.host],
        // panel.port может отсутствовать (панель опознана по MAC без ответа на
        // :21) — иначе в форму попадала строка "undefined". Discovery сканирует
        // порт 21, он же дефолт FTP.
        ["port", String(panel.port || 21)],
        ["path", "/datalog"],
        ["passive", "on"],
        ["web_scheme", panel.web_scheme || ""],
        // Панель сохраняется в режиме главной, в котором её нашли (CIP/ПОУ).
        ["equipment", currentHomeMode()],
      ],
      fields: [
        // «Weintek cMT-3C6F», можно поправить
        { label: "Название панели", name: "label", value: displayName },
        {
          label: "Пароль",
          name: "password",
          type: "password",
          required: true,
          placeholder: "111111",
          autocomplete: "current-password",
          focus: true,
        },
      ],
      submitLabel: "Добавить панель",
    });
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
    const subnet = subnetInput && !subnetInput.hidden ? subnetInput.value.trim() : "";
    setStatus(subnet ? `Сканирую ${subnet}…` : "Сканирую локальную сеть…");
    try {
      const response = await fetchWithTimeout("/api/ftp/discover", {
        method: "POST",
        timeout: 30000,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(subnet ? { subnet } : {}),
      });
      if (response.status === 400) {
        // Подсеть введена вручную и неверна — показываем причину от сервера.
        const problem = await response.json().catch(() => null);
        setStatus(problem?.detail || "Некорректная подсеть.");
        return;
      }
      if (!response.ok) {
        throw new Error("ftp-discover-failed");
      }
      const data = await response.json();
      const panels = Array.isArray(data.panels) ? data.panels : [];
      if (!panels.length) {
        // Перечисляем просканированные подсети: сразу видно, что «своей»
        // подсети панели среди них нет.
        const scanned = Array.isArray(data.networks) && data.networks.length
          ? `проверены подсети ${data.networks.join(", ")} (${data.scanned} адресов)`
          : `проверено адресов: ${data.scanned}`;
        if (!data.scanned) {
          setStatus("Не удалось определить локальную сеть.");
        } else if (data.ftp_hosts) {
          setStatus(
            `Панели Weintek не найдены. FTP-хостов в сети: ${data.ftp_hosts}, ` +
              `${scanned}. Панель в другой подсети (MAC-поиск не проходит за ` +
              `маршрутизатор) — укажите подсеть слева или добавьте вручную.`
          );
        } else {
          setStatus(
            `Панели не найдены, ${scanned}. Панель в другой подсети — укажите ` +
              `подсеть ниже или подключитесь вручную.`
          );
        }
        renderResults([]);
        if (subnetInput) {
          subnetInput.hidden = false; // даём указать подсеть за маршрутизатором
        }
        return;
      }
      setStatus("");  // список говорит сам за себя — без строки-счётчика
      if (subnetInput) {
        subnetInput.hidden = true;
      }
      renderResults(panels);
    } catch (_error) {
      // Инлайновый статус, не тост: ошибка относится к блоку поиска.
      setStatus("Не удалось выполнить поиск панели.");
      if (subnetInput) {
        subnetInput.hidden = false;
      }
    } finally {
      button.disabled = false;
    }
  });
}
