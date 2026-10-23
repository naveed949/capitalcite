import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";

const answeredQuestion = "What CET1 ratio did JPMorgan disclose?";
const refusalQuestion = "What CET1 ratio did Wells Fargo disclose for year-end 2026?";
const cancelQuestion = "Hold this retrieve so cancel can land";
const gateQuestion = "What CET1 ratio did JPMorgan disclose for 2024?";

test("answered citation chip opens the span viewer", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("demo-model")).toHaveText("deterministic demo model");
  await page.getByLabel("Question").fill(answeredQuestion);
  await page.getByLabel("Ticker").fill("JPM");
  await page.getByRole("button", { name: "Ask" }).click();
  await expect(page.getByTestId("answer")).not.toHaveText("", { timeout: 120000 });
  await page.getByRole("button", { name: /citation 1$/ }).click();
  await expect(page.getByTestId("span-viewer")).toBeFocused();
  await expect(page.getByTestId("span-doc")).not.toHaveText("");
  await expect(page.getByTestId("span-section")).not.toHaveText("");
  await expect(page.getByTestId("span-quote")).not.toHaveText("");
  await page.screenshot({ path: "test-results/answered.png", fullPage: true });
});

test("refusal shows the banner and no answer", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Question").fill(refusalQuestion);
  await page.getByLabel("Ticker").fill("WFC");
  await page.getByRole("button", { name: "Ask" }).click();
  await expect(page.getByTestId("refusal")).not.toHaveText("", { timeout: 120000 });
  await expect(page.getByTestId("answer")).toHaveCount(0);
  await page.screenshot({ path: "test-results/refused.png", fullPage: true });
});

test("gate denial appears on the agent timeline", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("radio", { name: "Agent" }).check();
  await page.getByLabel("Question").fill(gateQuestion);
  await page.getByRole("button", { name: "Ask" }).click();
  const denied = page.getByTestId("timeline").locator('[data-kind="denied"]');
  await expect(denied).toContainText("search_filings", { timeout: 120000 });
  await expect(denied).toContainText("unknown_tool");
  await page.screenshot({ path: "test-results/gate-denied.png", fullPage: true });
});

test("cancel stops the UI and the API run", async ({ page }) => {
  const logPath = process.env.CAPITALCITE_API_LOG;
  if (logPath === undefined || logPath.length === 0) {
    throw new Error("CAPITALCITE_API_LOG must point at the API log");
  }
  await page.goto("/");
  await page.getByLabel("Question").fill(cancelQuestion);
  const posted = page.waitForRequest(
    (req) => req.method() === "POST" && req.url().includes("/api/ask/stream"),
  );
  await page.getByRole("button", { name: "Ask" }).click();
  await posted;
  await expect(page.getByTestId("cancel")).toBeVisible();
  await page.waitForTimeout(500);
  await page.getByTestId("cancel").click();
  await expect(page.getByTestId("cancelled")).toHaveText("Cancelled");
  await expect(page.getByTestId("answer")).toHaveCount(0);
  await expect
    .poll(() => {
      try {
        return readFileSync(logPath, "utf8");
      } catch {
        return "";
      }
    }, { timeout: 15000 })
    .toContain("ask stream cancelled by client");
  await page.screenshot({ path: "test-results/cancelled.png", fullPage: true });
});
