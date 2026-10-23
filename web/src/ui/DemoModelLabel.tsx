import { DEMO_MODEL_LABEL, type AskModelName } from "../session/session";

export function DemoModelLabel(props: { model: AskModelName }) {
  if (props.model !== "fake") return null;
  return (
    <p data-testid="demo-model" className="text-sm text-zinc-600 dark:text-zinc-300">
      {DEMO_MODEL_LABEL}
    </p>
  );
}
