import { defineConfig } from "vitest/config";
import path from "path";

// 仅用于本地运行 FlowRunner 的样例流程验收，不参与 tauri 构建
export default defineConfig({
  resolve: {
    alias: {
      "@": path.join(__dirname, "./src/"),
    },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/__tests__/**/*.test.ts"],
  },
});
