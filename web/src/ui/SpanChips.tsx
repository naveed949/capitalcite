import type { FilingSpan } from "../session/session";

export function SpanChips(props: {
  spans: readonly FilingSpan[];
  onShowSpan: (span: FilingSpan) => void;
}) {
  return (
    <ul data-testid="span-chips" className="flex flex-wrap gap-2">
      {props.spans.map((span, index) => (
        <li key={`${span.docId}:${span.start}:${span.end}:${span.quoteStart}:${span.quoteEnd}`}>
          <button
            type="button"
            data-testid="span-chip"
            aria-label={`${span.section} citation ${index + 1}`}
            className="rounded-full border border-zinc-300 px-3 py-1 text-sm"
            onClick={() => props.onShowSpan(span)}
          >
            {span.section}
          </button>
        </li>
      ))}
    </ul>
  );
}
