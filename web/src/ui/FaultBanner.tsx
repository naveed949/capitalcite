import type { AskFault } from "../session/session";

export function FaultBanner(props: { fault: AskFault }) {
  const text = props.fault.kind === "http" ? `${props.fault.status} ${props.fault.detail}` : props.fault.detail;
  return (
    <p data-testid="fault" role="alert" className="rounded border border-red-300 bg-red-50 px-3 py-2 text-sm">
      {text}
    </p>
  );
}
