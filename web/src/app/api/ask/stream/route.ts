const DEFAULT_ORIGIN = "http://127.0.0.1:8000";

type HalfDuplexInit = RequestInit & { readonly duplex: "half" };

export async function POST(request: Request): Promise<Response> {
  const origin = process.env.CAPITALCITE_API_ORIGIN ?? DEFAULT_ORIGIN;
  const upstream = await fetch(new URL("/ask/stream", origin), forwardInit(request));
  const headers = new Headers();
  const contentType = upstream.headers.get("content-type");
  if (contentType !== null) headers.set("content-type", contentType);
  return new Response(upstream.body, { status: upstream.status, headers });
}

function forwardInit(request: Request): HalfDuplexInit {
  const contentType = request.headers.get("content-type") ?? "application/json";
  return {
    method: "POST",
    headers: { "content-type": contentType },
    body: request.body,
    signal: request.signal,
    duplex: "half",
  };
}

export function GET(): Response {
  return new Response(null, { status: 405 });
}
