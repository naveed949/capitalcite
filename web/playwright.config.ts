import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  workers: 1,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:3001",
    trace: "on",
    video: "off",
    browserName: "chromium",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
