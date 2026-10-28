// Состояние страницы от сервера (window.__WASH_APP__ из шаблона). Один общий
// изменяемый объект: applyWorkspacePayload обновляет его поля на лету.
export const appState = window.__WASH_APP__ || {};
