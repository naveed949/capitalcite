import { forwardRef } from "react";
import type { FilingSpan } from "../session/session";

export const SpanViewer = forwardRef<
  HTMLElement,
  {
    span: FilingSpan;
    onClose: () => void;
  }
>(function SpanViewer(props, ref) {
  const href = httpUrl(props.span.sourceUrl);
  return (
    <aside
      ref={ref}
      tabIndex={-1}
      data-testid="span-viewer"
      className="w-full rounded border border-zinc-300 p-4"
    >
      <p data-testid="span-doc" className="font-mono text-sm">
        {props.span.docId}
      </p>
      <p data-testid="span-section" className="text-sm">
        {props.span.section}
      </p>
      <p className="mt-3 leading-7">
        <mark
          data-testid="span-quote"
          data-quote-start={props.span.quoteStart}
          data-quote-end={props.span.quoteEnd}
        >
          {props.span.quote}
        </mark>
      </p>
      <p data-testid="span-offsets" className="mt-2 font-mono text-xs text-zinc-600">
        chunk {props.span.start}-{props.span.end} quote {props.span.quoteStart}-{props.span.quoteEnd}
      </p>
      {href !== undefined ? (
        <a data-testid="span-source" href={href} className="mt-2 block text-sm underline">
          {href}
        </a>
      ) : (
        <p data-testid="span-source" className="mt-2 text-sm">
          {props.span.sourceUrl}
        </p>
      )}
      <button type="button" className="mt-4 rounded border border-zinc-300 px-3 py-1 text-sm" onClick={props.onClose}>
        Close
      </button>
    </aside>
  );
});

function httpUrl(value: string): string | undefined {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return undefined;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return undefined;
  return url.toString();
}
