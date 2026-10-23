import { connection } from "next/server";
import { askModelFromEnv } from "@/session/session";
import { AskScreen } from "@/ui/AskScreen";
import { DemoModelLabel } from "@/ui/DemoModelLabel";

export const instant = false;

export default async function Home() {
  await connection();
  const model = askModelFromEnv(process.env.ASK_MODEL);
  return (
    <main className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-6 py-10">
      <h1 className="text-2xl font-semibold tracking-tight">Ask a filing question</h1>
      <DemoModelLabel model={model} />
      <AskScreen model={model} />
    </main>
  );
}
