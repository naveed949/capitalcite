import { describe, expect, it } from "vitest";
import {
  addSpan,
  citedClaim,
  filingSpan,
  initialSession,
  reduceSession,
  type AskInput,
  type CitedText,
  type FilingSpan,
  type Session,
} from "./session";

const input: AskInput = {
  question: "What CET1 ratio did JPMorgan disclose?",
  path: "rag",
  ticker: "JPM",
  period: "",
  model: "fake",
};

function span(): FilingSpan {
  const built = filingSpan({
    docId: "doc-1",
    section: "Item 1",
    quote: "CET1 ratio of 13.0 percent",
    start: 10,
    end: 40,
    quoteStart: 12,
    quoteEnd: 38,
    sourceUrl: "https://www.sec.gov/Archives/example",
  });
  if (built === undefined) throw new Error("fixture span");
  return built;
}

function claim() {
  const built = citedClaim("CET1 ratio of 13.0 percent", [span()]);
  if (built === undefined) throw new Error("fixture claim");
  return built;
}

type VariantHasAnswer<T> = T extends { answer: string } ? true : false;
type VariantHasBuffered<T> = T extends { buffered: string } ? true : false;
type VariantHasPending<T> = T extends { pendingText: string } ? true : false;
type SessionAnswerFlags = {
  [S in Session["status"]]: VariantHasAnswer<Extract<Session, { status: S }>>;
};
type SessionBufferedFlags = {
  [S in Session["status"]]: VariantHasBuffered<Extract<Session, { status: S }>>;
};
type SessionPendingFlags = {
  [S in Session["status"]]: VariantHasPending<Extract<Session, { status: S }>>;
};
type AnyAnswer = SessionAnswerFlags[keyof SessionAnswerFlags];
type AnyBuffered = SessionBufferedFlags[keyof SessionBufferedFlags];
type AnyPending = SessionPendingFlags[keyof SessionPendingFlags];
const sessionHasNoAnswerString: AnyAnswer extends false ? true : never = true;
const sessionHasNoBuffered: AnyBuffered extends false ? true : never = true;
const sessionHasNoPendingText: AnyPending extends false ? true : never = true;
void sessionHasNoAnswerString;
void sessionHasNoBuffered;
void sessionHasNoPendingText;

describe("addSpan", () => {
  it("keeps two quotes that share a chunk span", () => {
    const first = span();
    const second = filingSpan({
      docId: first.docId,
      section: first.section,
      quote: "another quote in the same chunk",
      start: first.start,
      end: first.end,
      quoteStart: 40,
      quoteEnd: 70,
      sourceUrl: first.sourceUrl,
    });
    if (second === undefined) throw new Error("second span");
    const next = addSpan([first], second);
    expect(next).toHaveLength(2);
    expect(next[1]?.quoteStart).toBe(40);
  });
});

describe("cited claim", () => {
  it("rejects a blank string and an empty span list", () => {
    expect(citedClaim("", [])).toBeUndefined();
    expect(citedClaim("text", [])).toBeUndefined();
    expect(citedClaim("   ", [span()])).toBeUndefined();
  });

  it("rejects a plain string assigned to CitedText", () => {
    // @ts-expect-error plain string is not CitedText
    const bad: CitedText = "hello";
    void bad;
  });
});

describe("reduceSession", () => {
  it("hides prose until a claim exists, then keeps the answered snapshot", () => {
    let session = reduceSession(initialSession, { type: "submit", input });
    session = reduceSession(session, { type: "span", span: span() });
    expect(session.status).toBe("asking");
    if (session.status !== "asking") return;
    expect(session.phase).toBe("citing");
    expect("claim" in session).toBe(false);
    expect("answer" in session).toBe(false);

    const snapshot = claim();
    session = reduceSession(session, { type: "claim", claim: snapshot });
    session = reduceSession(session, { type: "claim", claim: snapshot });
    expect(session.status).toBe("asking");
    if (session.status !== "asking" || session.phase !== "writing") return;
    expect(session.claim.text).toBe(snapshot.text);
    expect(session.claim.spans).toEqual(snapshot.spans);
    expect("answer" in session).toBe(false);

    session = reduceSession(session, { type: "answered", claim: snapshot });
    expect(session.status).toBe("answered");
    if (session.status !== "answered") return;
    expect(session.claim.text).toBe("CET1 ratio of 13.0 percent");
    expect("answer" in session).toBe(false);

    const again = reduceSession(session, { type: "claim", claim: snapshot });
    expect(again).toBe(session);
  });

  it("stores a refusal reason and no claim", () => {
    let session = reduceSession(initialSession, { type: "submit", input });
    session = reduceSession(session, { type: "refused", reason: "no passages were retrieved" });
    expect(session.status).toBe("refused");
    if (session.status !== "refused") return;
    expect(session.reason).toBe("no passages were retrieved");
    expect("claim" in session).toBe(false);
    expect("answer" in session).toBe(false);
  });

  it("records an audited deny on the timeline", () => {
    let session = reduceSession(initialSession, { type: "submit", input });
    session = reduceSession(session, {
      type: "denied",
      tool: "shell",
      detail: "no such tool",
      reason: "unknown_tool",
    });
    if (session.status !== "asking") throw new Error("expected asking");
    expect(session.timeline).toEqual([
      { kind: "denied", tool: "shell", detail: "no such tool", reason: "unknown_tool" },
    ]);
  });

  it("keeps the first fault", () => {
    let session = reduceSession(initialSession, { type: "submit", input });
    session = reduceSession(session, {
      type: "fault",
      fault: { kind: "http", status: 503, detail: "index unavailable" },
    });
    session = reduceSession(session, {
      type: "fault",
      fault: { kind: "transport", detail: "later" },
    });
    expect(session.status).toBe("failed");
    if (session.status !== "failed") return;
    expect(session.fault).toEqual({ kind: "http", status: 503, detail: "index unavailable" });
    expect(session.claim).toBeNull();
  });

  it("fails an asking session when the stream ends early", () => {
    let session = reduceSession(initialSession, { type: "submit", input });
    session = reduceSession(session, { type: "done" });
    expect(session.status).toBe("failed");
    if (session.status !== "failed") return;
    expect(session.fault.detail).toBe("stream ended before an answer");
  });
});
