// Реестр модальных окон: порядок закрытия по Escape (верхнее — первым) и класс
// modal-open на body. Сам список окон задаёт main.js через setOverlays — модули
// окон зовут только syncOverlayState и не знают друг о друге.

// Пары [корневой элемент, функция закрытия]; порядок — от верхнего окна к нижнему.
let overlays = [];

export function setOverlays(list) {
  overlays = list;
}

export function topOverlay() {
  return overlays.find(([root]) => !root.hidden) || null;
}

export function syncOverlayState() {
  document.body.classList.toggle("modal-open", topOverlay() !== null);
}
