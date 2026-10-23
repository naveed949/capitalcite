import { describe, expect, it } from "vitest";
import { initialSession, reduceSession, type AskInput, type DomainEvent } from "../session/session";
import { askStream } from "./askStream";

const input: AskInput = {
  question: "What CET1 ratio did JPMorgan disclose?",
  path: "rag",
  ticker: "JPM",
  period: "",
  model: "fake",
};

const citation = {
  doc_id: "doc-1",
  section: "Item 1",
  quote: "CET1 ratio of 15.1 percent",
  start: 10,
  end: 40,
  quote_start: 12,
  quote_end: 38,
  source_url: "https://www.sec.gov/Archives/example",
};

function sse(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function streamResponse(chunks: readonly string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return new Response(body, {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
}

async function collect(
  fetchImpl: typeof fetch,
  signal = new AbortController().signal,
): Promise<DomainEvent[]> {
  const events: DomainEvent[] = [];
  for await (const event of askStream(input, { signal, fetchImpl })) events.push(event);
  return events;
}

describe("askStream", () => {
  it("keeps an early delta out of events until a citation can form a claim", async () => {
    const text = "draft answer before citation";
    const events = await collect(async () =>
      streamResponse([
        sse("answer_delta", { event: "answer_delta", text }),
        sse("citation", { event: "citation", citation }),
      ]),
    );
    expect(events[0]?.type).toBe("span");
    expect(JSON.stringify(events[0])).not.toContain(text);
    expect(events[1]).toMatchObject({ type: "claim", claim: { text } });
  });

  it("rebuilds the answered claim from the response", async () => {
    const events = await collect(async () =>
      streamResponse([
        sse("answer_delta", { event: "answer_delta", text: "draft text" }),
        sse("citation", { event: "citation", citation }),
        sse("answered", {
          event: "answered",
          response: {
            kind: "answered",
            answer: "final cited text",
            citations: [citation],
          },
        }),
      ]),
    );
    const answered = events.find((event) => event.type === "answered");
    expect(answered).toMatchObject({ type: "answered", claim: { text: "final cited text" } });
  });

  it("reads a string 422 detail and a validation msg list", async () => {
    const sentence = await collect(async () =>
      jsonResponse(422, { detail: "period must be YYYY-MM-DD or a 4-digit year" }),
    );
    expect(sentence).toEqual([
      {
        type: "fault",
        fault: { kind: "http", status: 422, detail: "period must be YYYY-MM-DD or a 4-digit year" },
      },
    ]);

    const listed = await collect(async () =>
      jsonResponse(422, { detail: [{ msg: "question must not be blank" }, { msg: "field required" }] }),
    );
    expect(listed[0]).toMatchObject({
      type: "fault",
      fault: { kind: "http", status: 422, detail: "question must not be blank; field required" },
    });
  });

  it("yields cancelled when the signal aborts", async () => {
    const controller = new AbortController();
    const fetchImpl: typeof fetch = (_url, init) =>
      new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => {
          reject(new DOMException("The operation was aborted.", "AbortError"));
        });
      });
    const pending = askStream(input, { signal: controller.signal, fetchImpl });
    const next = pending.next();
    controller.abort();
    const result = await next;
    expect(result.value).toEqual({ type: "cancelled" });
  });

  it("maps an audited deny and rejects gate_error", async () => {
    const denied = await collect(async () =>
      streamResponse([
        sse("tool_denied", {
          event: "tool_denied",
          reason: "unknown_tool",
          trace: { tool: "shell", args: {}, ok: false, detail: "no such tool" },
        }),
      ]),
    );
    expect(denied[0]).toEqual({
      type: "denied",
      tool: "shell",
      detail: "no such tool",
      reason: "unknown_tool",
    });

    const gate = await collect(async () =>
      streamResponse([
        sse("tool_denied", {
          event: "tool_denied",
          reason: "gate_error",
          trace: { tool: "search_filings", args: {}, ok: false, detail: "gate" },
        }),
      ]),
    );
    expect(gate).toEqual([{ type: "fault", fault: { kind: "transport", detail: "invalid event frame" } }]);
    expect(gate.some((event) => event.type === "denied")).toBe(false);
  });

  it("drops the preview when the run is refused", async () => {
    const events = await collect(async () =>
      streamResponse([
        sse("answer_delta", { event: "answer_delta", text: "preview" }),
        sse("citation", { event: "citation", citation }),
        sse("refused", {
          event: "refused",
          response: { kind: "refused", refusal_reason: "no passages were retrieved", citations: [] },
        }),
      ]),
    );
    let session = reduceSession(initialSession, { type: "submit", input });
    for (const event of events) session = reduceSession(session, event);
    expect(session.status).toBe("refused");
    if (session.status !== "refused") return;
    expect(session.reason).toBe("no passages were retrieved");
    expect("claim" in session).toBe(false);
  });

  it("parses one frame that arrives in two chunks", async () => {
    const frame = sse("done", { event: "done" });
    const mid = Math.floor(frame.length / 2);
    const events = await collect(async () => streamResponse([frame.slice(0, mid), frame.slice(mid)]));
    expect(events).toEqual([{ type: "done" }]);
  });

  it("omits a blank ticker and period from the posted body", async () => {
    let posted = "";
    const fetchImpl: typeof fetch = async (_url, init) => {
      posted = String(init?.body ?? "");
      return jsonResponse(422, { detail: "stop" });
    };
    await collect(fetchImpl);
    const body = JSON.parse(posted) as Record<string, unknown>;
    expect(body).toEqual({
      question: input.question,
      path: "rag",
      model: "fake",
      ticker: "JPM",
    });
    expect(body).not.toHaveProperty("period");
    expect(body).not.toHaveProperty("k");
    expect(body).not.toHaveProperty("form");
  });
});
