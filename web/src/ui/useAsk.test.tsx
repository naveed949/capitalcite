import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useAsk } from "./useAsk";

const { askStream } = vi.hoisted(() => ({ askStream: vi.fn() }));

vi.mock("../api", () => ({ askStream }));

describe("useAsk", () => {
  it("aborts the in-flight request after a transport fault", async () => {
    let signal: AbortSignal | undefined;
    askStream.mockImplementation(async function* (
      _input: unknown,
      options: { signal: AbortSignal },
    ) {
      signal = options.signal;
      yield { type: "fault" as const, fault: { kind: "transport" as const, detail: "invalid event frame" } };
    });
    const { result } = renderHook(() => useAsk("fake"));
    act(() => {
      result.current.submit({
        question: "What CET1 ratio did JPMorgan disclose?",
        path: "rag",
        ticker: "JPM",
        period: "",
      });
    });
    await waitFor(() => {
      expect(result.current.session.status).toBe("failed");
    });
    expect(signal?.aborted).toBe(true);
    expect(result.current.session.status).not.toBe("cancelled");
  });
});
