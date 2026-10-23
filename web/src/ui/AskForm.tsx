"use client";

import { useState } from "react";
import { preparedQuestion, type AskPath, type QuestionDraft } from "../session/session";

export function AskForm(props: {
  asking: boolean;
  onSubmit: (draft: QuestionDraft) => void;
  onCancel: () => void;
}) {
  const [question, setQuestion] = useState("");
  const [path, setPath] = useState<AskPath>("rag");
  const [ticker, setTicker] = useState("");
  const [period, setPeriod] = useState("");
  const ready = preparedQuestion(question) !== undefined;

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (!ready) return;
        props.onSubmit({
          question,
          path,
          ticker: path === "agent" ? "" : ticker,
          period: path === "agent" ? "" : period,
        });
      }}
    >
      <label className="flex flex-col gap-1 text-sm font-medium">
        Question
        <textarea
          data-testid="question"
          className="min-h-28 rounded border border-zinc-300 bg-transparent px-3 py-2 text-base font-normal"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
        />
      </label>
      <fieldset className="flex gap-4 text-sm">
        <legend className="mb-1 font-medium">Path</legend>
        <label className="flex items-center gap-2">
          <input
            type="radio"
            name="path"
            value="rag"
            checked={path === "rag"}
            onChange={() => setPath("rag")}
          />
          Retrieval
        </label>
        <label className="flex items-center gap-2">
          <input
            type="radio"
            name="path"
            value="agent"
            checked={path === "agent"}
            onChange={() => setPath("agent")}
          />
          Agent
        </label>
      </fieldset>
      {path === "rag" ? (
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="flex flex-col gap-1 text-sm font-medium">
            Ticker
            <input
              data-testid="ticker"
              className="rounded border border-zinc-300 bg-transparent px-3 py-2 font-normal"
              value={ticker}
              onChange={(event) => setTicker(event.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm font-medium">
            Period
            <input
              data-testid="period"
              className="rounded border border-zinc-300 bg-transparent px-3 py-2 font-normal"
              value={period}
              onChange={(event) => setPeriod(event.target.value)}
            />
          </label>
        </div>
      ) : null}
      <div className="flex gap-3">
        <button
          type="submit"
          data-testid="ask"
          className="rounded bg-zinc-900 px-4 py-2 text-sm text-white disabled:opacity-40 dark:bg-zinc-100 dark:text-zinc-900"
          disabled={!ready}
        >
          Ask
        </button>
        {props.asking ? (
          <button
            type="button"
            data-testid="cancel"
            className="rounded border border-zinc-300 px-4 py-2 text-sm"
            onClick={props.onCancel}
          >
            Cancel
          </button>
        ) : null}
      </div>
    </form>
  );
}
