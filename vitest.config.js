import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // Чистые функции графика DOM не требуют — быстрый node-рантайм.
    environment: "node",
    include: ["tests/js/**/*.test.js"],
  },
});
