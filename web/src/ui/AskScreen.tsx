"use client";

import { useState } from "react";
import {
  visibleClaim,
  visibleSpans,
  type AskModelName,
  type FilingSpan,
  type QuestionDraft,
  type Session,
} from "../session/session";
import { AnswerBody } from "./AnswerBody";
import { AskForm } from "./AskForm";
import { FaultBanner } from "./FaultBanner";
import { RefusalBanner } from "./RefusalBanner";
import { SpanChips } from "./SpanChips";
import { SpanViewer } from "./SpanViewer";
import { Timeline } from "./Timeline";
import { useAsk } from "./useAsk";

type SpanId = {
  readonly docId: string;
  readonly start: number;
  readonly end: number;
  readonly quoteStart: number;
  readonly quoteEnd: number;
};

export function AskScreen(props: { model: AskModelName }) {
  const ask = useAsk(props.model);
  return (
    <AskSessionView session={ask.session} onSubmit={ask.submit} onCancel={ask.cancel} />
  );
}

export function AskSessionView(props: {
  session: Session;
  onSubmit: (draft: QuestionDraft) => void;
  onCancel: () => void;
}) {
  const [opened, setOpened] = useState<SpanId | null>(null);
  const spans = visibleSpans(props.session);
  const claim = visibleClaim(props.session);
  const citing = props.session.status === "asking" && props.session.phase === "citing";
  const openedSpan =
    opened === null
      ? undefined
      : spans.find(
          (span) =>
            span.docId === opened.docId &&
            span.start === opened.start &&
            span.end === opened.end &&
            span.quoteStart === opened.quoteStart &&
            span.quoteEnd === opened.quoteEnd,
        );
  const timeline = props.session.status === "idle" ? [] : props.session.timeline;

  function showSpan(span: FilingSpan) {
    setOpened({
      docId: span.docId,
      start: span.start,
      end: span.end,
      quoteStart: span.quoteStart,
      quoteEnd: span.quoteEnd,
    });
  }

  return (
    <div className="flex flex-col gap-6">
      <AskForm asking={props.session.status === "asking"} onSubmit={props.onSubmit} onCancel={props.onCancel} />
      <Timeline items={timeline} />
      {citing && spans.length > 0 ? <SpanChips spans={spans} onShowSpan={showSpan} /> : null}
      {claim !== null ? <AnswerBody claim={claim} onShowSpan={showSpan} /> : null}
      {props.session.status === "refused" ? <RefusalBanner reason={props.session.reason} /> : null}
      {props.session.status === "failed" ? <FaultBanner fault={props.session.fault} /> : null}
      {props.session.status === "cancelled" ? (
        <p data-testid="cancelled" role="status">
          Cancelled
        </p>
      ) : null}
      {openedSpan !== undefined ? <SpanViewer span={openedSpan} onClose={() => setOpened(null)} /> : null}
    </div>
  );
}
