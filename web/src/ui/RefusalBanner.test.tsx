import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { initialSession, reduceSession, type AskInput } from "../session/session";
import { AskSessionView } from "./AskScreen";
import { RefusalBanner } from "./RefusalBanner";

const input: AskInput = {
  question: "What CET1 ratio did Wells Fargo disclose for year-end 2026?",
  path: "rag",
  ticker: "WFC",
  period: "",
  model: "fake",
};

describe("RefusalBanner", () => {
  it("shows the reason and does not take an answer", () => {
    render(<RefusalBanner reason="no passages were retrieved" />);
    expect(screen.getByTestId("refusal")).toHaveTextContent("no passages were retrieved");
    expect(screen.queryByTestId("answer")).toBeNull();
  });

  it("renders a refused session without an answer node", () => {
    let session = reduceSession(initialSession, { type: "submit", input });
    session = reduceSession(session, { type: "refused", reason: "no passages were retrieved" });
    render(<AskSessionView session={session} onSubmit={() => undefined} onCancel={() => undefined} />);
    expect(screen.getByTestId("refusal")).toHaveTextContent("no passages were retrieved");
    expect(screen.queryByTestId("answer")).toBeNull();
  });
});
