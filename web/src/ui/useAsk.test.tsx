import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { QuestionDraft } from "../session/session";
import { useAsk } from "./useAsk";

const draft: QuestionDraft = {
  question: "What CET1 ratio did JPMorgan disclose?",
  path: "rag",
  ticker: "JPM",
  period: "",
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useAsk", () => {
  it("publishes a fault and then aborts the request", async () => {
    let signal: AbortSignal | undefined;
    let cancelled = 0;
    const encoder = new TextEncoder();
    vi.stubGlobal("fetch", async (_url: string, init?: RequestInit) => {
      signal = init?.signal ?? undefined;
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(encoder.encode("not-a-frame\n\n"));
        },
        cancel() {
          cancelled += 1;
        },
      });
      return new Response(body, {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      });
    });

    const { result } = renderHook(() => useAsk("fake"));
    act(() => {
      result.current.submit(draft);
    });

    await waitFor(() => {
      expect(result.current.session.status).toBe("failed");
    });
    expect(signal?.aborted).toBe(true);
    expect(cancelled).toBe(1);
  });

  it("aborts the previous controller when a newer run starts", async () => {
    const signals: AbortSignal[] = [];
    let release: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    vi.stubGlobal("fetch", async (_url: string, init?: RequestInit) => {
      if (init?.signal != null) signals.push(init.signal);
      await gate;
      return new Response(JSON.stringify({ detail: "stop" }), {
        status: 422,
        headers: { "content-type": "application/json" },
      });
    });

    const { result } = renderHook(() => useAsk("fake"));
    act(() => {
      result.current.submit(draft);
    });
    await waitFor(() => {
      expect(signals).toHaveLength(1);
    });
    act(() => {
      result.current.submit({ ...draft, question: "What buffer did JPM describe?" });
    });
    expect(signals[0]?.aborted).toBe(true);
    release?.();
    await waitFor(() => {
      expect(result.current.session.status).toBe("failed");
    });
  });
});
