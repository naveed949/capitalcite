export function RefusalBanner(props: { reason: string }) {
  return (
    <p data-testid="refusal" role="status" className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm">
      {props.reason}
    </p>
  );
}
