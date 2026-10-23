import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { citedClaim, filingSpan, initialSession, reduceSession, type AskInput } from "../session/session";
import { AskSessionView } from "./AskScreen";
import { AnswerBody } from "./AnswerBody";
import { SpanViewer } from "./SpanViewer";

const input: AskInput = {
  question: "What CET1 ratio did JPMorgan disclose?",
  path: "rag",
  ticker: "JPM",
  period: "",
  model: "fake",
};

function fixtureSpan() {
  const span = filingSpan({
    docId: "doc-1",
    section: "Item 1",
    quote: "CET1 ratio of 13.0 percent",
    start: 10,
    end: 40,
    quoteStart: 12,
    quoteEnd: 38,
    sourceUrl: "https://www.sec.gov/Archives/example",
  });
  if (span === undefined) throw new Error("fixture span");
  return span;
}

function fixtureClaim() {
  const claim = citedClaim("CET1 ratio of 13.0 percent", [fixtureSpan()]);
  if (claim === undefined) throw new Error("fixture claim");
  return claim;
}

describe("AnswerBody", () => {
  it("renders the claim text and a chip that opens the span", () => {
    const claim = fixtureClaim();
    const seen: string[] = [];
    render(<AnswerBody claim={claim} onShowSpan={(span) => seen.push(span.docId)} />);
    expect(screen.getByTestId("answer")).toHaveTextContent("CET1 ratio of 13.0 percent");
    fireEvent.click(screen.getByTestId("span-chip"));
    expect(seen).toEqual(["doc-1"]);
  });

  it("hides ticker and period on the agent path", () => {
    render(<AskSessionView session={initialSession} onSubmit={() => undefined} onCancel={() => undefined} />);
    expect(screen.getByTestId("ticker")).toBeInTheDocument();
    expect(screen.getByTestId("period")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "Agent" }));
    expect(screen.queryByTestId("ticker")).toBeNull();
    expect(screen.queryByTestId("period")).toBeNull();
    expect(screen.getByRole("radio", { name: "Retrieval" })).toBeInTheDocument();
  });

  it("does not mount an answer while the session is only citing", () => {
    let session = reduceSession(initialSession, { type: "submit", input });
    session = reduceSession(session, { type: "span", span: fixtureSpan() });
    render(<AskSessionView session={session} onSubmit={() => undefined} onCancel={() => undefined} />);
    expect(screen.queryByTestId("answer")).toBeNull();
    expect(screen.getByTestId("span-chip")).toHaveTextContent("Item 1");
  });
});

describe("SpanViewer", () => {
  it("shows the document, section, and highlighted quote range", () => {
    render(<SpanViewer span={fixtureSpan()} onClose={() => undefined} />);
    expect(screen.getByTestId("span-doc")).toHaveTextContent("doc-1");
    expect(screen.getByTestId("span-section")).toHaveTextContent("Item 1");
    const quote = screen.getByTestId("span-quote");
    expect(quote.tagName).toBe("MARK");
    expect(quote).toHaveTextContent("CET1 ratio of 13.0 percent");
    expect(quote).toHaveAttribute("data-quote-start", "12");
    expect(quote).toHaveAttribute("data-quote-end", "38");
    expect(screen.getByTestId("span-source")).toHaveAttribute(
      "href",
      "https://www.sec.gov/Archives/example",
    );
  });

  it("does not link a non-http source", () => {
    const span = filingSpan({
      docId: "doc-1",
      section: "Item 1",
      quote: "CET1 ratio of 13.0 percent",
      start: 10,
      end: 40,
      quoteStart: 12,
      quoteEnd: 38,
      sourceUrl: "javascript:alert(1)",
    });
    if (span === undefined) throw new Error("fixture span");
    render(<SpanViewer span={span} onClose={() => undefined} />);
    expect(screen.getByTestId("span-source").tagName).not.toBe("A");
  });
});
