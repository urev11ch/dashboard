import { defineConfig } from "@playwright/test";

// E2E-сценарии OptiCIP Dashboard. Сервер поднимается автоматически (web-режим,
// loopback), браузер — системный Chrome (channel: "chrome"), чтобы не тянуть
// собственные сборки Playwright.
export default defineConfig({
  testDir: "tests/e2e",
  timeout: 30000,
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:8765",
    channel: "chrome",
    trace: "off",
    // --no-sandbox: в контейнерах/CI без user-namespaces Chrome иначе не стартует.
    launchOptions: { args: ["--no-sandbox"] },
  },
  webServer: {
    // Локально — .venv, в CI глобальный python: переопределяется через PYTHON.
    command: `${process.env.PYTHON || ".venv/bin/python"} run_wash_ui.py`,
    url: "http://127.0.0.1:8765/",
    reuseExistingServer: true,
    timeout: 60000,
    env: { HOST: "127.0.0.1", PORT: "8765" },
  },
});
