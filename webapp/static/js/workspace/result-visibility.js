// Показ колонки «Результат мойки»: выбор пользователя в localStorage и класс
// wash-result-hidden на body.

const WASH_RESULT_PREF_KEY = "opticipShowWashResultV1";

// По умолчанию результат мойки НЕ показывается: вердикт зависит от настроек
// (шаг окончания, нормативы концентрации), и до того, как их задали под
// конкретную станцию, колонка вводит в заблуждение. Показывается только по
// явному включению — ключ со значением "1". Тем, кто уже включил тумблер
// раньше, ключ сохраняет выбор (значение не менялось).
export function isWashResultVisible() {
  try {
    return window.localStorage.getItem(WASH_RESULT_PREF_KEY) === "1";
  } catch (error) {
    return false;
  }
}

export function applyWashResultVisibility() {
  document.body.classList.toggle("wash-result-hidden", !isWashResultVisible());
}

export function setWashResultVisible(visible) {
  try {
    window.localStorage.setItem(WASH_RESULT_PREF_KEY, visible ? "1" : "0");
  } catch (error) {
    /* localStorage может быть недоступен — просто применяем к текущей сессии */
  }
  applyWashResultVisibility();
}
