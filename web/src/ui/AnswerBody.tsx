import type { CitedClaim, FilingSpan } from "../session/session";
import { SpanChips } from "./SpanChips";

export function AnswerBody(props: {
  claim: CitedClaim;
  onShowSpan: (span: FilingSpan) => void;
}) {
  return (
    <article className="flex flex-col gap-3">
      <p data-testid="answer" aria-live="polite" className="text-base leading-7">
        {props.claim.text}
      </p>
      <SpanChips spans={props.claim.spans} onShowSpan={props.onShowSpan} />
    </article>
  );
}
