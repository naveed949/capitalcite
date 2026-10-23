const DEFAULT_ORIGIN = "http://127.0.0.1:8000";
const PROXY_BODY_MAX_BYTES = 16 * 1024;
const PAYLOAD_TOO_LARGE = '{"detail":"payload too large"}';
const UPSTREAM_UNAVAILABLE = '{"detail":"upstream unavailable"}';

function pinnedModel(value: string | undefined): "fake" | "openai" {
  return value === "openai" ? "openai" : "fake";
}

function asArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  const copy = new ArrayBuffer(bytes.byteLength);
  new Uint8Array(copy).set(bytes);
  return copy;
}

function rewriteBody(raw: Uint8Array, model: "fake" | "openai"): ArrayBuffer {
  try {
    const parsed: unknown = JSON.parse(new TextDecoder().decode(raw));
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      return asArrayBuffer(raw);
    }
    return asArrayBuffer(new TextEncoder().encode(JSON.stringify({ ...parsed, model })));
  } catch {
    return asArrayBuffer(raw);
  }
}

export async function POST(request: Request): Promise<Response> {
  const raw = new Uint8Array(await request.arrayBuffer());
  if (raw.byteLength > PROXY_BODY_MAX_BYTES) {
    return new Response(PAYLOAD_TOO_LARGE, {
      status: 413,
      headers: { "content-type": "application/json" },
    });
  }
  const origin = process.env.CAPITALCITE_API_ORIGIN ?? DEFAULT_ORIGIN;
  const body = rewriteBody(raw, pinnedModel(process.env.ASK_MODEL));
  try {
    const upstream = await fetch(new URL("/ask/stream", origin), {
      method: "POST",
      headers: { "content-type": request.headers.get("content-type") ?? "application/json" },
      body,
      signal: request.signal,
    });
    const headers = new Headers();
    const contentType = upstream.headers.get("content-type");
    if (contentType !== null) headers.set("content-type", contentType);
    return new Response(upstream.body, { status: upstream.status, headers });
  } catch (error) {
    if (request.signal.aborted) throw error;
    return new Response(UPSTREAM_UNAVAILABLE, {
      status: 502,
      headers: { "content-type": "application/json" },
    });
  }
}

export function GET(): Response {
  return new Response(null, { status: 405 });
}
