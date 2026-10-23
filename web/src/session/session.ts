declare const citedTextBrand: unique symbol;
declare const docIdBrand: unique symbol;
declare const quoteBrand: unique symbol;

export type CitedText = string & { readonly [citedTextBrand]: true };
export type DocId = string & { readonly [docIdBrand]: true };
export type Quote = string & { readonly [quoteBrand]: true };

export type AskModelName = "fake" | "openai";
export type AskPath = "rag" | "agent";

export const DEMO_MODEL_LABEL = "deterministic demo model";

export type FilingSpan = {
  readonly docId: DocId;
  readonly section: string;
  readonly quote: Quote;
  readonly start: number;
  readonly end: number;
  readonly quoteStart: number;
  readonly quoteEnd: number;
  readonly sourceUrl: string;
};

export type CitedClaim = {
  readonly text: CitedText;
  readonly spans: readonly [FilingSpan, ...FilingSpan[]];
};

export type AuditedDenyReason =
  | "unknown_tool"
  | "invalid_args"
  | "budget_exceeded"
  | "args_too_large"
  | "policy_load_failed";

export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonArgs
  | readonly JsonValue[];

export type JsonArgs = { readonly [key: string]: JsonValue };

export type PlannedStep = {
  readonly tool: string;
  readonly args: JsonArgs;
};

export type TimelineItem =
  | { readonly kind: "plan"; readonly steps: readonly [PlannedStep, ...PlannedStep[]] }
  | { readonly kind: "call"; readonly tool: string; readonly args: JsonArgs }
  | { readonly kind: "result"; readonly tool: string; readonly detail: string }
  | {
      readonly kind: "denied";
      readonly tool: string;
      readonly detail: string;
      readonly reason: AuditedDenyReason;
    };

export type AskInput = {
  readonly question: string;
  readonly path: AskPath;
  readonly ticker: string;
  readonly period: string;
  readonly model: AskModelName;
};

export type QuestionDraft = {
  readonly question: string;
  readonly path: AskPath;
  readonly ticker: string;
  readonly period: string;
};

export type AskFault =
  | { readonly kind: "http"; readonly status: 422 | 500 | 503; readonly detail: string }
  | { readonly kind: "transport"; readonly detail: string };

export type DomainEvent =
  | { readonly type: "submit"; readonly input: AskInput }
  | { readonly type: "plan"; readonly steps: readonly [PlannedStep, ...PlannedStep[]] }
  | { readonly type: "call"; readonly tool: string; readonly args: JsonArgs }
  | { readonly type: "result"; readonly tool: string; readonly detail: string }
  | {
      readonly type: "denied";
      readonly tool: string;
      readonly detail: string;
      readonly reason: AuditedDenyReason;
    }
  | { readonly type: "span"; readonly span: FilingSpan }
  | { readonly type: "claim"; readonly claim: CitedClaim }
  | { readonly type: "answered"; readonly claim: CitedClaim }
  | { readonly type: "refused"; readonly reason: string }
  | { readonly type: "fault"; readonly fault: AskFault }
  | { readonly type: "cancelled" }
  | { readonly type: "done" };

export type Session =
  | { readonly status: "idle" }
  | {
      readonly status: "asking";
      readonly phase: "citing";
      readonly input: AskInput;
      readonly timeline: readonly TimelineItem[];
      readonly spans: readonly FilingSpan[];
    }
  | {
      readonly status: "asking";
      readonly phase: "writing";
      readonly input: AskInput;
      readonly timeline: readonly TimelineItem[];
      readonly claim: CitedClaim;
    }
  | {
      readonly status: "answered";
      readonly input: AskInput;
      readonly timeline: readonly TimelineItem[];
      readonly claim: CitedClaim;
    }
  | {
      readonly status: "refused";
      readonly input: AskInput;
      readonly timeline: readonly TimelineItem[];
      readonly reason: string;
    }
  | {
      readonly status: "failed";
      readonly input: AskInput;
      readonly timeline: readonly TimelineItem[];
      readonly fault: AskFault;
      readonly claim: CitedClaim | null;
    }
  | {
      readonly status: "cancelled";
      readonly input: AskInput;
      readonly timeline: readonly TimelineItem[];
      readonly claim: CitedClaim | null;
    };

export const initialSession: Session = { status: "idle" };

export function askModelFromEnv(value: string | undefined): AskModelName {
  if (value === "openai") return "openai";
  return "fake";
}

export function preparedQuestion(raw: string): string | undefined {
  const trimmed = raw.trim();
  if (trimmed.length === 0) return undefined;
  return trimmed;
}

function isDocId(value: string): value is DocId {
  return value.length > 0;
}

function isQuote(value: string): value is Quote {
  return value.length > 0;
}

function isCitedText(value: string): value is CitedText {
  return value.length > 0;
}

function isIntegerOffset(value: number): boolean {
  return Number.isInteger(value);
}

function hasSpan(
  spans: readonly FilingSpan[],
): spans is readonly [FilingSpan, ...FilingSpan[]] {
  return spans.length > 0;
}

export function filingSpan(fields: {
  docId: string;
  section: string;
  quote: string;
  start: number;
  end: number;
  quoteStart: number;
  quoteEnd: number;
  sourceUrl: string;
}): FilingSpan | undefined {
  const docId = fields.docId.trim();
  const section = fields.section.trim();
  const quote = fields.quote.trim();
  const sourceUrl = fields.sourceUrl.trim();
  if (!isDocId(docId) || !isQuote(quote) || section.length === 0 || sourceUrl.length === 0) {
    return undefined;
  }
  const { start, end, quoteStart, quoteEnd } = fields;
  if (
    !isIntegerOffset(start) ||
    !isIntegerOffset(end) ||
    !isIntegerOffset(quoteStart) ||
    !isIntegerOffset(quoteEnd)
  ) {
    return undefined;
  }
  if (end < start || quoteEnd < quoteStart) return undefined;
  return { docId, section, quote, start, end, quoteStart, quoteEnd, sourceUrl };
}

export function citedClaim(
  text: string,
  spans: readonly FilingSpan[],
): CitedClaim | undefined {
  const trimmed = text.trim();
  if (!isCitedText(trimmed) || !hasSpan(spans)) return undefined;
  return { text: trimmed, spans };
}

export function addSpan(
  spans: readonly FilingSpan[],
  span: FilingSpan,
): readonly FilingSpan[] {
  for (const existing of spans) {
    if (
      existing.docId === span.docId &&
      existing.start === span.start &&
      existing.end === span.end &&
      existing.quoteStart === span.quoteStart &&
      existing.quoteEnd === span.quoteEnd
    ) {
      return spans;
    }
  }
  return [...spans, span];
}

export function visibleSpans(session: Session): readonly FilingSpan[] {
  switch (session.status) {
    case "idle":
    case "refused":
      return [];
    case "asking":
      return session.phase === "citing" ? session.spans : session.claim.spans;
    case "answered":
      return session.claim.spans;
    case "failed":
    case "cancelled":
      return session.claim === null ? [] : session.claim.spans;
    default: {
      const unreachable: never = session;
      return unreachable;
    }
  }
}

export function visibleClaim(session: Session): CitedClaim | null {
  switch (session.status) {
    case "idle":
    case "refused":
      return null;
    case "asking":
      return session.phase === "writing" ? session.claim : null;
    case "answered":
      return session.claim;
    case "failed":
    case "cancelled":
      return session.claim;
    default: {
      const unreachable: never = session;
      return unreachable;
    }
  }
}

function withTimeline(session: Session, item: TimelineItem): Session {
  if (session.status !== "asking") return session;
  return { ...session, timeline: [...session.timeline, item] };
}

function onSpan(session: Session, span: FilingSpan): Session {
  if (session.status !== "asking" || session.phase !== "citing") return session;
  return { ...session, spans: addSpan(session.spans, span) };
}

function onClaim(session: Session, claim: CitedClaim): Session {
  if (session.status !== "asking") return session;
  if (session.phase === "citing") {
    return {
      status: "asking",
      phase: "writing",
      input: session.input,
      timeline: session.timeline,
      claim,
    };
  }
  return { ...session, claim };
}

function onAnswered(session: Session, claim: CitedClaim): Session {
  if (session.status !== "asking") return session;
  return {
    status: "answered",
    input: session.input,
    timeline: session.timeline,
    claim,
  };
}

function onRefused(session: Session, reason: string): Session {
  if (session.status !== "asking") return session;
  return {
    status: "refused",
    input: session.input,
    timeline: session.timeline,
    reason,
  };
}

function onFault(session: Session, fault: AskFault): Session {
  if (session.status === "failed") return session;
  if (session.status !== "asking" && session.status !== "answered") return session;
  return {
    status: "failed",
    input: session.input,
    timeline: session.timeline,
    fault,
    claim: visibleClaim(session),
  };
}

function onCancelled(session: Session): Session {
  if (session.status !== "asking") return session;
  return {
    status: "cancelled",
    input: session.input,
    timeline: session.timeline,
    claim: session.phase === "writing" ? session.claim : null,
  };
}

function onDone(session: Session): Session {
  if (session.status !== "asking") return session;
  return {
    status: "failed",
    input: session.input,
    timeline: session.timeline,
    fault: { kind: "transport", detail: "stream ended before an answer" },
    claim: session.phase === "writing" ? session.claim : null,
  };
}

export function reduceSession(session: Session, event: DomainEvent): Session {
  switch (event.type) {
    case "submit":
      return {
        status: "asking",
        phase: "citing",
        input: event.input,
        timeline: [],
        spans: [],
      };
    case "plan":
      return withTimeline(session, { kind: "plan", steps: event.steps });
    case "call":
      return withTimeline(session, { kind: "call", tool: event.tool, args: event.args });
    case "result":
      return withTimeline(session, { kind: "result", tool: event.tool, detail: event.detail });
    case "denied":
      return withTimeline(session, {
        kind: "denied",
        tool: event.tool,
        detail: event.detail,
        reason: event.reason,
      });
    case "span":
      return onSpan(session, event.span);
    case "claim":
      return onClaim(session, event.claim);
    case "answered":
      return onAnswered(session, event.claim);
    case "refused":
      return onRefused(session, event.reason);
    case "fault":
      return onFault(session, event.fault);
    case "cancelled":
      return onCancelled(session);
    case "done":
      return onDone(session);
    default: {
      const unreachable: never = event;
      return unreachable;
    }
  }
}
