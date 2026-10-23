"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { askStream } from "../api";
import {
  initialSession,
  preparedQuestion,
  reduceSession,
  type AskInput,
  type AskModelName,
  type QuestionDraft,
  type Session,
} from "../session/session";

export type AskController = {
  readonly session: Session;
  readonly submit: (draft: QuestionDraft) => void;
  readonly cancel: () => void;
};

export function useAsk(model: AskModelName): AskController {
  const [session, setSession] = useState<Session>(initialSession);
  const run = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      abortRef.current?.abort();
    };
  }, []);

  const submit = useCallback(
    (draft: QuestionDraft) => {
      const runId = run.current + 1;
      run.current = runId;
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      const question = preparedQuestion(draft.question) ?? "";
      const input: AskInput = {
        question,
        path: draft.path,
        ticker: draft.ticker,
        period: draft.period,
        model,
      };
      void pump(runId, input, controller, run, mounted, setSession);
    },
    [model],
  );

  const cancel = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  return { session, submit, cancel };
}

async function pump(
  runId: number,
  input: AskInput,
  controller: AbortController,
  run: { current: number },
  mounted: { current: boolean },
  setSession: (session: Session) => void,
): Promise<void> {
  const publish = (next: Session) => {
    if (!mounted.current || runId !== run.current) return;
    setSession(next);
  };
  let current = reduceSession(initialSession, { type: "submit", input });
  publish(current);
  for await (const event of askStream(input, { signal: controller.signal })) {
    if (runId !== run.current) {
      controller.abort();
      return;
    }
    current = reduceSession(current, event);
    publish(current);
    if (event.type === "fault") {
      controller.abort();
      return;
    }
  }
}
