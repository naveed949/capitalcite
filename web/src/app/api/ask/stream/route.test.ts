import { createServer, type Server } from "node:http";
import { afterEach, describe, expect, it } from "vitest";
import { GET, POST } from "./route";

let server: Server | undefined;

afterEach(async () => {
  delete process.env.CAPITALCITE_API_ORIGIN;
  delete process.env.ASK_MODEL;
  if (server !== undefined) {
    await new Promise<void>((resolve, reject) => {
      server?.close((error) => (error ? reject(error) : resolve()));
    });
    server = undefined;
  }
});

describe("ask stream route", () => {
  it("returns 405 for GET", () => {
    expect(GET().status).toBe(405);
  });

  it("reads the first SSE chunk before the upstream writes the second", async () => {
    let secondWritten = false;
    let releaseSecond: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      releaseSecond = resolve;
    });
    server = createServer((req, res) => {
      if (req.url !== "/ask/stream") {
        res.writeHead(404);
        res.end();
        return;
      }
      res.writeHead(200, { "content-type": "text/event-stream" });
      res.write("event: citation\ndata: {}\n\n");
      void gate.then(() => {
        secondWritten = true;
        res.write('event: done\ndata: {"event":"done"}\n\n');
        res.end();
      });
    });
    await new Promise<void>((resolve) => {
      server?.listen(0, "127.0.0.1", resolve);
    });
    const address = server.address();
    if (address === null || typeof address === "string") throw new Error("missing port");
    process.env.CAPITALCITE_API_ORIGIN = `http://127.0.0.1:${address.port}`;

    const response = await POST(
      new Request("http://127.0.0.1:3001/api/ask/stream", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: "{}",
      }),
    );
    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toContain("text/event-stream");
    const reader = response.body?.getReader();
    if (reader === undefined) throw new Error("missing body");
    const first = await reader.read();
    expect(secondWritten).toBe(false);
    expect(new TextDecoder().decode(first.value)).toContain("citation");
    releaseSecond();
    const rest = await reader.read();
    expect(secondWritten).toBe(true);
    expect(new TextDecoder().decode(rest.value)).toContain("done");
    await reader.cancel();
  });

  it("returns 413 when the body is larger than 16 KiB", async () => {
    const body = JSON.stringify({ question: "x".repeat(20 * 1024) });
    const response = await POST(
      new Request("http://127.0.0.1:3001/api/ask/stream", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body,
      }),
    );
    expect(response.status).toBe(413);
    expect(await response.json()).toEqual({ detail: "payload too large" });
  });

  it("returns 502 with a generic body when the API is unreachable", async () => {
    process.env.CAPITALCITE_API_ORIGIN = "http://127.0.0.1:1";
    const response = await POST(
      new Request("http://127.0.0.1:3001/api/ask/stream", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ question: "What CET1 ratio did JPMorgan disclose?" }),
      }),
    );
    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ detail: "upstream unavailable" });
  });

  it("pins model from ASK_MODEL and ignores the client value", async () => {
    let posted = "";
    server = createServer((req, res) => {
      if (req.url !== "/ask/stream") {
        res.writeHead(404);
        res.end();
        return;
      }
      const chunks: Buffer[] = [];
      req.on("data", (chunk: Buffer) => {
        chunks.push(chunk);
      });
      req.on("end", () => {
        posted = Buffer.concat(chunks).toString("utf8");
        res.writeHead(200, { "content-type": "text/event-stream" });
        res.end('event: done\ndata: {"event":"done"}\n\n');
      });
    });
    await new Promise<void>((resolve) => {
      server?.listen(0, "127.0.0.1", resolve);
    });
    const address = server.address();
    if (address === null || typeof address === "string") throw new Error("missing port");
    process.env.CAPITALCITE_API_ORIGIN = `http://127.0.0.1:${address.port}`;
    delete process.env.ASK_MODEL;

    const response = await POST(
      new Request("http://127.0.0.1:3001/api/ask/stream", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          question: "What CET1 ratio did JPMorgan disclose?",
          model: "openai",
        }),
      }),
    );
    expect(response.status).toBe(200);
    expect(JSON.parse(posted)).toMatchObject({
      question: "What CET1 ratio did JPMorgan disclose?",
      model: "fake",
    });

    process.env.ASK_MODEL = "openai";
    const openai = await POST(
      new Request("http://127.0.0.1:3001/api/ask/stream", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          question: "What CET1 ratio did JPMorgan disclose?",
          model: "fake",
        }),
      }),
    );
    expect(openai.status).toBe(200);
    expect(JSON.parse(posted).model).toBe("openai");

    process.env.ASK_MODEL = "OpenAI";
    const other = await POST(
      new Request("http://127.0.0.1:3001/api/ask/stream", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          question: "What CET1 ratio did JPMorgan disclose?",
          model: "openai",
        }),
      }),
    );
    expect(other.status).toBe(200);
    expect(JSON.parse(posted).model).toBe("fake");
  });
});
