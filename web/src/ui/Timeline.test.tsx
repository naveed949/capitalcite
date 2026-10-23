import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { initialSession, reduceSession, type AskInput } from "../session/session";
import { AskSessionView } from "./AskScreen";
import { Timeline } from "./Timeline";

const input: AskInput = {
  question: "What CET1 ratio did JPMorgan disclose?",
  path: "agent",
  ticker: "",
  period: "",
  model: "fake",
};

describe("Timeline", () => {
  it("shows the audited deny reason", () => {
    render(
      <Timeline
        items={[
          {
            kind: "denied",
            tool: "shell",
            detail: "no such tool",
            reason: "unknown_tool",
          },
        ]}
      />,
    );
    expect(screen.getByTestId("timeline")).toHaveTextContent("unknown_tool");
    expect(screen.getByTestId("timeline")).toHaveAttribute("aria-live", "polite");
  });

  it("shows a denied row from a folded session", () => {
    let session = reduceSession(initialSession, { type: "submit", input });
    session = reduceSession(session, {
      type: "denied",
      tool: "shell",
      detail: "no such tool",
      reason: "unknown_tool",
    });
    render(<AskSessionView session={session} onSubmit={() => undefined} onCancel={() => undefined} />);
    expect(screen.getByTestId("timeline")).toHaveTextContent("unknown_tool");
    expect(screen.queryByTestId("answer")).toBeNull();
  });
});
