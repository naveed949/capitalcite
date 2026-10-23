import type { TimelineItem } from "../session/session";

export function Timeline(props: { items: readonly TimelineItem[] }) {
  return (
    <ol data-testid="timeline" className="flex flex-col gap-2 text-sm">
      {props.items.map((item, index) => (
        <li key={index} data-kind={item.kind} className="rounded border border-zinc-200 px-3 py-2">
          <TimelineRow item={item} />
        </li>
      ))}
    </ol>
  );
}

function TimelineRow(props: { item: TimelineItem }) {
  const item = props.item;
  switch (item.kind) {
    case "plan":
      return <span>Plan {item.steps.map((step) => step.tool).join(", ")}</span>;
    case "call":
      return (
        <span>
          Call {item.tool}
        </span>
      );
    case "result":
      return (
        <span>
          {item.tool} {item.detail}
        </span>
      );
    case "denied":
      return (
        <span>
          {item.tool} {item.detail} {item.reason}
        </span>
      );
    default: {
      const unreachable: never = item;
      return unreachable;
    }
  }
}
