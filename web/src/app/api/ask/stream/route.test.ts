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

  it("returns 413 from Content-Length without reading the body", async () => {
    let pulled = 0;
    let cancelled = 0;
    const stream = new ReadableStream<Uint8Array>({
      pull(controller) {
        pulled += 1024;
        controller.enqueue(new Uint8Array(1024).fill(0x61));
      },
      cancel() {
        cancelled += 1;
      },
    });
    const response = await POST(
      new Request("http://127.0.0.1:3001/api/ask/stream", {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "content-length": String(300 * 1024 * 1024),
        },
        duplex: "half",
        body: stream,
      } as RequestInit),
    );
    expect(response.status).toBe(413);
    expect(await response.json()).toEqual({ detail: "payload too large" });
    expect(pulled).toBe(0);
    expect(cancelled).toBe(1);
  });

  it("returns 413 without draining a chunked body past 16 KiB", async () => {
    const chunk = new Uint8Array(4 * 1024).fill(0x61);
    let pulled = 0;
    let cancelled = 0;
    const stream = new ReadableStream<Uint8Array>({
      pull(controller) {
        pulled += chunk.byteLength;
        controller.enqueue(chunk);
      },
      cancel() {
        cancelled += 1;
      },
    });
    const response = await POST(
      new Request("http://127.0.0.1:3001/api/ask/stream", {
        method: "POST",
        headers: { "content-type": "application/json" },
        duplex: "half",
        body: stream,
      } as RequestInit),
    );
    expect(response.status).toBe(413);
    expect(await response.json()).toEqual({ detail: "payload too large" });
    expect(cancelled).toBe(1);
    expect(pulled).toBeGreaterThan(16 * 1024);
    expect(pulled).toBeLessThanOrEqual(20 * 1024);
  });

  it.each(['{"question":"What CET1 ratio did JPMorgan disclose?","model":"openai","x":NaN}', "[]", '"x"', "null"])(
    "returns 400 and does not call upstream for %s",
    async (body) => {
      let called = 0;
      server = createServer((_req, res) => {
        called += 1;
        res.writeHead(200);
        res.end();
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
          body,
        }),
      );
      expect(response.status).toBe(400);
      expect(await response.json()).toEqual({ detail: "invalid body" });
      expect(called).toBe(0);
    },
  );

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

  it("sends application/json upstream regardless of the client header", async () => {
    let contentType = "";
    server = createServer((req, res) => {
      if (req.url !== "/ask/stream") {
        res.writeHead(404);
        res.end();
        return;
      }
      contentType = String(req.headers["content-type"] ?? "");
      res.writeHead(200, { "content-type": "text/event-stream" });
      res.end('event: done\ndata: {"event":"done"}\n\n');
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
        headers: { "content-type": "text/plain" },
        body: JSON.stringify({ question: "What CET1 ratio did JPMorgan disclose?" }),
      }),
    );
    expect(response.status).toBe(200);
    expect(contentType).toBe("application/json");
  });
});
