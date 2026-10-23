import { createServer, type Server } from "node:http";
import { afterEach, describe, expect, it } from "vitest";
import { GET, POST } from "./route";

let server: Server | undefined;

afterEach(async () => {
  delete process.env.CAPITALCITE_API_ORIGIN;
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
});
