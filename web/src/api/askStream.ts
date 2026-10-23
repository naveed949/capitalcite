import type { components, operations } from "./schema";
import {
  addSpan,
  citedClaim,
  filingSpan,
  preparedQuestion,
  type AskFault,
  type AskInput,
  type AuditedDenyReason,
  type DomainEvent,
  type FilingSpan,
  type JsonArgs,
  type JsonValue,
  type PlannedStep,
} from "../session/session";

type SchemaCitation = components["schemas"]["Citation"];
type WireEvent =
  operations["post_ask_stream_ask_stream_post"]["responses"][200]["content"]["text/event-stream"];
type WireName = WireEvent["event"];

const CITATION_KEYS = [
  "doc_id",
  "start",
  "end",
  "quote",
  "quote_start",
  "quote_end",
  "section",
  "source_url",
] as const;

type CitationKeysMatch = Exclude<keyof SchemaCitation, (typeof CITATION_KEYS)[number]> extends never
  ? Exclude<(typeof CITATION_KEYS)[number], keyof SchemaCitation> extends never
    ? true
    : never
  : never;

const citationKeysMatch: CitationKeysMatch = true;
void citationKeysMatch;

const WIRE_NAMES = [
  "plan",
  "tool_call",
  "tool_result",
  "tool_denied",
  "citation",
  "answer_delta",
  "answered",
  "refused",
  "error",
  "done",
] as const;

type WireNamesMatch = Exclude<WireName, (typeof WIRE_NAMES)[number]> extends never
  ? Exclude<(typeof WIRE_NAMES)[number], WireName> extends never
    ? true
    : never
  : never;

const wireNamesMatch: WireNamesMatch = true;
void wireNamesMatch;

type Frame =
  | { readonly ok: true; readonly name: WireName; readonly body: unknown }
  | { readonly ok: false };

type Fold = {
  readonly pendingText: string;
  readonly spans: readonly FilingSpan[];
  readonly events: readonly DomainEvent[];
  readonly stop: boolean;
};

export async function* askStream(
  input: AskInput,
  options: {
    readonly signal: AbortSignal;
    readonly fetchImpl?: typeof fetch;
  },
): AsyncGenerator<DomainEvent, void, undefined> {
  const question = preparedQuestion(input.question);
  if (question === undefined) {
    yield httpFault(422, "question must not be blank");
    return;
  }

  const fetchImpl = options.fetchImpl ?? fetch;
  const payload: {
    question: string;
    path: AskInput["path"];
    model: AskInput["model"];
    ticker?: string;
    period?: string;
  } = {
    question,
    path: input.path,
    model: input.model,
  };
  const ticker = input.ticker.trim();
  const period = input.period.trim();
  if (ticker.length > 0) payload.ticker = ticker;
  if (period.length > 0) payload.period = period;

  let response: Response;
  try {
    response = await fetchImpl("/api/ask/stream", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        accept: "text/event-stream",
      },
      body: JSON.stringify(payload),
      signal: options.signal,
    });
  } catch (error) {
    yield faultOrCancel(error, options.signal);
    return;
  }

  if (!isEventStream(response)) {
    yield await faultFromHttp(response);
    return;
  }
  if (response.body === null) {
    yield transportFault("empty stream");
    return;
  }

  let pendingText = "";
  let spans: readonly FilingSpan[] = [];
  try {
    for await (const raw of sseFrames(response.body, options.signal)) {
      const frame = parseFrame(raw);
      if (!frame.ok) {
        yield transportFault("invalid event frame");
        return;
      }
      const fold = applyFrame(frame.name, frame.body, pendingText, spans);
      pendingText = fold.pendingText;
      spans = fold.spans;
      for (const event of fold.events) yield event;
      if (fold.stop) return;
    }
  } catch (error) {
    yield faultOrCancel(error, options.signal);
  }
}

function httpFault(status: 422 | 500 | 503, detail: string): DomainEvent {
  return { type: "fault", fault: { kind: "http", status, detail } };
}

function transportFault(detail: string): DomainEvent {
  return { type: "fault", fault: { kind: "transport", detail } };
}

function faultOrCancel(error: unknown, signal: AbortSignal): DomainEvent {
  if (signal.aborted || isAbortError(error)) return { type: "cancelled" };
  const detail = error instanceof Error && error.message.length > 0 ? error.message : "request failed";
  return transportFault(detail);
}

function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error && error.name === "AbortError";
}

function isEventStream(response: Response): boolean {
  const header = response.headers.get("content-type") ?? "";
  return header.toLowerCase().includes("text/event-stream");
}

async function faultFromHttp(response: Response): Promise<DomainEvent> {
  const detail = await readDetail(response);
  if (response.status === 422 || response.status === 500 || response.status === 503) {
    return httpFault(response.status, detail);
  }
  return transportFault(detail.length > 0 ? detail : `HTTP ${response.status}`);
}

async function readDetail(response: Response): Promise<string> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return "request failed";
  }
  return detailText(body);
}

function detailText(body: unknown): string {
  if (typeof body !== "object" || body === null || !("detail" in body)) return "request failed";
  const detail = body.detail;
  if (typeof detail === "string") return detail;
  if (!Array.isArray(detail)) return "request failed";
  const messages: string[] = [];
  for (const item of detail) {
    if (typeof item === "object" && item !== null && "msg" in item && typeof item.msg === "string") {
      if (item.msg.length > 0) messages.push(item.msg);
    }
  }
  return messages.length > 0 ? messages.join("; ") : "request failed";
}

async function* sseFrames(
  body: ReadableStream<Uint8Array>,
  signal: AbortSignal,
): AsyncGenerator<string> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      if (signal.aborted) throw new DOMException("The operation was aborted.", "AbortError");
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true }).replaceAll("\r\n", "\n");
      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        if (frame.trim().length > 0) yield frame;
        boundary = buffer.indexOf("\n\n");
      }
    }
    buffer += decoder.decode().replaceAll("\r\n", "\n");
    if (buffer.trim().length > 0) yield buffer;
  } finally {
    reader.releaseLock();
  }
}

function parseFrame(raw: string): Frame {
  const lines = raw.replaceAll("\r\n", "\n").split("\n");
  let sseEvent = "";
  const dataLines: string[] = [];
  for (const line of lines) {
    if (line.length === 0 || line.startsWith(":")) continue;
    if (line.startsWith("event:")) {
      sseEvent = line.slice("event:".length).trim();
      continue;
    }
    if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trim());
      continue;
    }
    return { ok: false };
  }
  if (sseEvent.length === 0 || dataLines.length !== 1) return { ok: false };
  if (dataLines[0]?.includes("\n")) return { ok: false };
  let payload: unknown;
  try {
    payload = JSON.parse(dataLines[0] ?? "");
  } catch {
    return { ok: false };
  }
  if (typeof payload !== "object" || payload === null || !("event" in payload)) return { ok: false };
  if (typeof payload.event !== "string" || payload.event !== sseEvent) return { ok: false };
  if (!isWireName(payload.event)) return { ok: false };
  return { ok: true, name: payload.event, body: payload };
}

function isWireName(value: string): value is WireName {
  for (const name of WIRE_NAMES) {
    if (value === name) return true;
  }
  return false;
}

function applyFrame(
  name: WireName,
  body: unknown,
  pendingText: string,
  spans: readonly FilingSpan[],
): Fold {
  switch (name) {
    case "plan":
      return one(pendingText, spans, planEvent(body));
    case "tool_call":
      return one(pendingText, spans, callEvent(body));
    case "tool_result":
      return one(pendingText, spans, resultEvent(body));
    case "tool_denied":
      return one(pendingText, spans, deniedEvent(body));
    case "citation":
      return citationFold(body, pendingText, spans);
    case "answer_delta":
      return deltaFold(body, pendingText, spans);
    case "answered":
      return answeredFold(body, spans);
    case "refused":
      return refusedFold(body);
    case "error":
      return one(pendingText, spans, errorEvent(body));
    case "done":
      return { pendingText, spans, events: [{ type: "done" }], stop: false };
    default: {
      const unreachable: never = name;
      return unreachable;
    }
  }
}

function one(
  pendingText: string,
  spans: readonly FilingSpan[],
  event: DomainEvent | undefined,
): Fold {
  if (event === undefined) {
    return { pendingText, spans, events: [transportFault("invalid event frame")], stop: true };
  }
  return { pendingText, spans, events: [event], stop: false };
}

function planEvent(body: unknown): DomainEvent | undefined {
  if (!isRecord(body) || !Array.isArray(body.plan)) return undefined;
  const steps = pendingSteps(body.plan);
  if (steps === undefined) return undefined;
  return { type: "plan", steps };
}

function callEvent(body: unknown): DomainEvent | undefined {
  if (!isRecord(body)) return undefined;
  const step = pendingStep(body.step);
  if (step === undefined) return undefined;
  return { type: "call", tool: step.tool, args: step.args };
}

function resultEvent(body: unknown): DomainEvent | undefined {
  if (!isRecord(body)) return undefined;
  const trace = readTrace(body.trace);
  if (trace === undefined) return undefined;
  return { type: "result", tool: trace.tool, detail: trace.detail };
}

function deniedEvent(body: unknown): DomainEvent | undefined {
  if (!isRecord(body) || typeof body.reason !== "string") return undefined;
  if (!isAuditedDenyReason(body.reason)) return undefined;
  const trace = readTrace(body.trace);
  if (trace === undefined) return undefined;
  return { type: "denied", tool: trace.tool, detail: trace.detail, reason: body.reason };
}

function citationFold(body: unknown, pendingText: string, spans: readonly FilingSpan[]): Fold {
  if (!isRecord(body)) {
    return { pendingText, spans, events: [transportFault("invalid event frame")], stop: true };
  }
  const span = spanFromCitation(body.citation);
  if (span === undefined) {
    return { pendingText, spans, events: [transportFault("invalid citation")], stop: true };
  }
  const nextSpans = addSpan(spans, span);
  const events: DomainEvent[] = [{ type: "span", span }];
  const claim = citedClaim(pendingText, nextSpans);
  if (claim !== undefined) events.push({ type: "claim", claim });
  return { pendingText, spans: nextSpans, events, stop: false };
}

function deltaFold(body: unknown, pendingText: string, spans: readonly FilingSpan[]): Fold {
  if (!isRecord(body) || typeof body.text !== "string" || body.text.length === 0) {
    return { pendingText, spans, events: [transportFault("invalid event frame")], stop: true };
  }
  const nextText = pendingText + body.text;
  const claim = citedClaim(nextText, spans);
  const events: DomainEvent[] = claim === undefined ? [] : [{ type: "claim", claim }];
  return { pendingText: nextText, spans, events, stop: false };
}

function answeredFold(body: unknown, spans: readonly FilingSpan[]): Fold {
  if (!isRecord(body) || !isRecord(body.response)) {
    return { pendingText: "", spans, events: [transportFault("invalid event frame")], stop: true };
  }
  const response = body.response;
  if (typeof response.answer !== "string" || !Array.isArray(response.citations)) {
    return {
      pendingText: "",
      spans,
      events: [transportFault("answered without a cited claim")],
      stop: false,
    };
  }
  let built: readonly FilingSpan[] = [];
  for (const citation of response.citations) {
    const span = spanFromCitation(citation);
    if (span === undefined) {
      return { pendingText: "", spans, events: [transportFault("invalid citation")], stop: true };
    }
    built = addSpan(built, span);
  }
  const claim = citedClaim(response.answer, built);
  if (claim === undefined) {
    return {
      pendingText: "",
      spans,
      events: [transportFault("answered without a cited claim")],
      stop: false,
    };
  }
  return { pendingText: "", spans: built, events: [{ type: "answered", claim }], stop: false };
}

function refusedFold(body: unknown): Fold {
  if (!isRecord(body) || !isRecord(body.response) || typeof body.response.refusal_reason !== "string") {
    return { pendingText: "", spans: [], events: [transportFault("invalid event frame")], stop: true };
  }
  const reason = body.response.refusal_reason.trim();
  if (reason.length === 0) {
    return { pendingText: "", spans: [], events: [transportFault("invalid event frame")], stop: true };
  }
  return { pendingText: "", spans: [], events: [{ type: "refused", reason }], stop: false };
}

function errorEvent(body: unknown): DomainEvent | undefined {
  if (!isRecord(body) || typeof body.detail !== "string" || body.detail.length === 0) return undefined;
  if (body.status !== 422 && body.status !== 500 && body.status !== 503) return undefined;
  const fault: AskFault = { kind: "http", status: body.status, detail: body.detail };
  return { type: "fault", fault };
}

function spanFromCitation(value: unknown): FilingSpan | undefined {
  if (!isRecord(value)) return undefined;
  if (!CITATION_KEYS.every((key) => key in value)) return undefined;
  if (
    typeof value.doc_id !== "string" ||
    typeof value.section !== "string" ||
    typeof value.quote !== "string" ||
    typeof value.source_url !== "string" ||
    typeof value.start !== "number" ||
    typeof value.end !== "number" ||
    typeof value.quote_start !== "number" ||
    typeof value.quote_end !== "number"
  ) {
    return undefined;
  }
  return filingSpan({
    docId: value.doc_id,
    section: value.section,
    quote: value.quote,
    start: value.start,
    end: value.end,
    quoteStart: value.quote_start,
    quoteEnd: value.quote_end,
    sourceUrl: value.source_url,
  });
}

function pendingSteps(value: readonly unknown[]): readonly [PlannedStep, ...PlannedStep[]] | undefined {
  const steps: PlannedStep[] = [];
  for (const item of value) {
    const step = pendingStep(item);
    if (step === undefined) return undefined;
    steps.push(step);
  }
  if (!isNonEmptySteps(steps)) return undefined;
  return steps;
}

function isNonEmptySteps(
  steps: readonly PlannedStep[],
): steps is readonly [PlannedStep, ...PlannedStep[]] {
  return steps.length > 0;
}

function pendingStep(value: unknown): PlannedStep | undefined {
  if (!isRecord(value) || typeof value.tool !== "string" || value.tool.length === 0) return undefined;
  if (value.status !== "pending") return undefined;
  const args = readArgs(value.args);
  if (args === undefined) return undefined;
  return { tool: value.tool, args };
}

function readTrace(value: unknown): { readonly tool: string; readonly detail: string } | undefined {
  if (!isRecord(value) || typeof value.tool !== "string" || value.tool.length === 0) return undefined;
  if (typeof value.detail !== "string") return undefined;
  return { tool: value.tool, detail: value.detail };
}

function readArgs(value: unknown): JsonArgs | undefined {
  if (value === undefined) return {};
  if (!isJsonArgs(value)) return undefined;
  return value;
}

function isJsonArgs(value: unknown): value is JsonArgs {
  if (!isRecord(value)) return false;
  return Object.values(value).every(isJsonValue);
}

function isJsonValue(value: unknown): value is JsonValue {
  if (value === null || typeof value === "string" || typeof value === "boolean") return true;
  if (typeof value === "number") return Number.isFinite(value);
  if (Array.isArray(value)) return value.every(isJsonValue);
  if (isRecord(value)) return Object.values(value).every(isJsonValue);
  return false;
}

function isAuditedDenyReason(value: string): value is AuditedDenyReason {
  switch (value) {
    case "unknown_tool":
    case "invalid_args":
    case "budget_exceeded":
    case "args_too_large":
    case "policy_load_failed":
      return true;
    default:
      return false;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
