import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

function arg(name) {
  const index = process.argv.indexOf(name);
  const value = process.argv[index + 1];
  if (index === -1 || value === undefined) {
    process.stderr.write(`missing ${name}\n`);
    process.exit(1);
  }
  return value;
}

const baseUrl = arg("--base-url");
const out = arg("--out");
mkdirSync(out, { recursive: true });

function write(name, text) {
  const body = text.endsWith("\n") ? text : `${text}\n`;
  writeFileSync(resolve(out, name), body);
}

let chromium;
try {
  ({ chromium } = await import("playwright"));
} catch (error) {
  process.stderr.write(`playwright import failed: ${error}\n`);
  process.exit(2);
}

let browser;
try {
  browser = await chromium.launch({ headless: true });
} catch (bundled) {
  try {
    browser = await chromium.launch({ headless: true, channel: "chrome" });
  } catch (installed) {
    process.stderr.write(`chromium launch failed: ${bundled}\n${installed}\n`);
    process.exit(2);
  }
}

const retrievalQuestion = "What CET1 ratio did JPMorgan disclose?";
const agentQuestion = "What CET1 ratio did JPMorgan disclose for 2024?";
const refusalQuestion = "What CET1 ratio did Wells Fargo disclose for year-end 2026?";

try {
  const labelPage = await browser.newPage();
  await labelPage.goto(baseUrl, { waitUntil: "domcontentloaded" });
  const label = labelPage.getByTestId("demo-model");
  await label.waitFor({ timeout: 15000 });
  const labelText = (await label.innerText()).trim();
  if (labelText !== "deterministic demo model") {
    throw new Error(`demo label was ${JSON.stringify(labelText)}`);
  }
  write("web.label.txt", labelText);

  await labelPage.getByLabel("Question").fill(retrievalQuestion);
  await labelPage.getByLabel("Ticker").fill("JPM");
  await labelPage.getByRole("button", { name: "Ask" }).click();
  await labelPage.getByTestId("answer").waitFor({ timeout: 120000 });
  const answer = (await labelPage.getByTestId("answer").innerText()).trim();
  if (answer.length === 0) throw new Error("empty answer");
  await labelPage.getByTestId("span-chip").first().click();
  await labelPage.getByTestId("span-viewer").waitFor();
  const docId = (await labelPage.getByTestId("span-doc").innerText()).trim();
  const section = (await labelPage.getByTestId("span-section").innerText()).trim();
  const quote = (await labelPage.getByTestId("span-quote").innerText()).trim();
  if (docId.length === 0 || section.length === 0 || quote.length === 0) {
    throw new Error("span viewer missing docId, section, or quote");
  }
  write("web.answered.txt", `${answer}\n${docId}\n${section}\n${quote}`);
  await labelPage.screenshot({
    path: resolve(out, "web.answered.png"),
    fullPage: true,
  });
  await labelPage.close();

  const agentPage = await browser.newPage();
  await agentPage.goto(baseUrl, { waitUntil: "domcontentloaded" });
  await agentPage.getByRole("radio", { name: "Agent" }).check();
  if ((await agentPage.getByTestId("ticker").count()) !== 0) {
    throw new Error("ticker input still visible on the agent path");
  }
  await agentPage.getByLabel("Question").fill(agentQuestion);
  await agentPage.getByRole("button", { name: "Ask" }).click();
  await agentPage.getByTestId("timeline").getByText("search_filings").waitFor({ timeout: 120000 });
  write("web.agent.txt", await agentPage.getByTestId("timeline").innerText());
  await agentPage.screenshot({
    path: resolve(out, "web.agent.png"),
    fullPage: true,
  });
  await agentPage.close();

  const refusalPage = await browser.newPage();
  await refusalPage.goto(baseUrl, { waitUntil: "domcontentloaded" });
  await refusalPage.getByLabel("Question").fill(refusalQuestion);
  await refusalPage.getByLabel("Ticker").fill("WFC");
  await refusalPage.getByRole("button", { name: "Ask" }).click();
  await refusalPage.getByTestId("refusal").waitFor({ timeout: 120000 });
  const reason = (await refusalPage.getByTestId("refusal").innerText()).trim();
  if (reason.length === 0) throw new Error("empty refusal");
  if ((await refusalPage.getByTestId("answer").count()) !== 0) {
    throw new Error("answer node present on a refusal");
  }
  write("web.refused.txt", reason);
  await refusalPage.screenshot({
    path: resolve(out, "web.refused.png"),
    fullPage: true,
  });
  await refusalPage.close();

  const cancelPage = await browser.newPage();
  await cancelPage.goto(baseUrl, { waitUntil: "domcontentloaded" });
  let release = () => {};
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  await cancelPage.route("**/api/ask/stream", async (route) => {
    await gate;
    await route.abort();
  });
  await cancelPage.getByLabel("Question").fill(retrievalQuestion);
  await cancelPage.getByLabel("Ticker").fill("JPM");
  await cancelPage.getByRole("button", { name: "Ask" }).click();
  await cancelPage.getByTestId("cancel").click();
  await cancelPage.getByTestId("cancelled").waitFor({ timeout: 15000 });
  if ((await cancelPage.getByTestId("answer").count()) !== 0) {
    throw new Error("answer node present after cancel");
  }
  write("web.cancel.txt", "cancelled");
  release();
  await cancelPage.close();
} catch (error) {
  process.stderr.write(`${error}\n`);
  process.exitCode = 1;
} finally {
  await browser.close();
}
