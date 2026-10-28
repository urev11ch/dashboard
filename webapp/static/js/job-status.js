// Плашка фоновой задачи (загрузка/обновление данных): прогресс, отмена,
// подписка на SSE-поток статуса с откатом на опрос. Действия с рабочей
// областью по завершении задачи передаёт main.js (параметр workspace).

import { apiJson, fetchWithTimeout } from "./core/api.js";
import { showToast } from "./core/toast.js";

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

export function fetchWorkspaceJobStatus() {
  return apiJson("/api/workspace-job");
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

export function updateWorkspaceJobUi(status, options = {}) {
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

async function handleTerminalWorkspaceJob(status, workspace) {
  // Фоновое автообновление обрабатывается отдельным поллером (без оверлея и
  // перезагрузки), поэтому здесь его игнорируем.
  if (status.background) {
    return;
  }

  updateWorkspaceJobUi(status, { keepVisible: true });

  if (!workspace.hasWorkspace()) {
    window.location.reload();
    return;
  }

  if (status.status === "completed") {
    try {
      await workspace.hydrateWorkspaceData({ resetScroll: false });
      workspace.setScreenError("");
      setWorkspaceJobVisible(false);
      showToast("Данные обновлены", "success");
    } catch (_error) {
      window.location.reload();
    }
    return;
  }

  try {
    const payload = await workspace.fetchWorkspaceData();
    if (payload?.has_analysis) {
      workspace.applyWorkspacePayload(payload, { resetScroll: false });
    }
  } catch (_error) {
    // Keep the current screen data as-is when the refresh result cannot be fetched.
  }

  workspace.setScreenError(status.error || status.message || "");
  setWorkspaceJobVisible(false);

  if (status.status === "cancelled") {
    showToast("Обновление отменено", "info");
  } else {
    showToast(status.error || status.message || "Не удалось обновить данные.", "error");
  }
}

export function initWorkspaceJobStatusFeed(initialJobStatus, workspace) {
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
      await handleTerminalWorkspaceJob(status, workspace);
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

  return {
    ensureMonitoring() {
      if (!startStream()) {
        startPollingFallback();
      }
    },
  };
}
