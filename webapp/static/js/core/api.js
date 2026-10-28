// Запросы к локальному API: fetch с таймаутом и единый разбор JSON-ответов.
// Модуль самодостаточен (только fetch) — его зовут и экраны до гейта hasWorkspace.

// Обёртка над fetch с таймаутом. Без неё зависший бэкенд/FTP (TCP без RST)
// никогда не резолвит промис: кнопка «Обновляю…»/«Сохраняю PDF…» залипает
// навсегда (finally не срабатывает). По таймауту — abort и понятная ошибка.
export const DEFAULT_FETCH_TIMEOUT_MS = 30000;
export function fetchWithTimeout(resource, options) {
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

// Ошибка ответа сервера: текст из `detail` (так отвечает бэкенд), иначе запасной.
export async function responseError(response, fallback) {
  let message = fallback || `Сервер ответил ошибкой ${response.status}.`;
  try {
    const payload = await response.json();
    if (payload?.detail) {
      message = String(payload.detail);
    }
  } catch (_error) {
    // Тело не JSON — остаётся запасной текст.
  }
  return new Error(message);
}

// Единый JSON-запрос к API: таймаут, проверка статуса, разбор ответа.
export async function apiJson(url, { method = "GET", body, timeout, errorMessage } = {}) {
  const options = { method, headers: { Accept: "application/json" } };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  if (timeout !== undefined) {
    options.timeout = timeout;
  }
  const response = await fetchWithTimeout(url, options);
  if (!response.ok) {
    throw await responseError(response, errorMessage);
  }
  return response.json();
}
