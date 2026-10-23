const DEFAULT_ORIGIN = "http://127.0.0.1:8000";
const PROXY_BODY_MAX_BYTES = 16 * 1024;
const PAYLOAD_TOO_LARGE = '{"detail":"payload too large"}';
const UPSTREAM_UNAVAILABLE = '{"detail":"upstream unavailable"}';
const INVALID_BODY = '{"detail":"invalid body"}';

function pinnedModel(value: string | undefined): "fake" | "openai" {
  return value === "openai" ? "openai" : "fake";
}

function asArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  const copy = new ArrayBuffer(bytes.byteLength);
  new Uint8Array(copy).set(bytes);
  return copy;
}

function jsonResponse(status: number, body: string): Response {
  return new Response(body, {
    status,
    headers: { "content-type": "application/json" },
  });
}

function declaredLength(request: Request): number | undefined {
  const raw = request.headers.get("content-length");
  if (raw === null || raw.length === 0) return undefined;
  const n = Number(raw);
  if (!Number.isFinite(n) || n < 0) return undefined;
  return n;
}

async function readCappedBody(request: Request): Promise<Uint8Array | "too-large"> {
  const declared = declaredLength(request);
  if (declared !== undefined && declared > PROXY_BODY_MAX_BYTES) {
    if (request.body !== null) {
      await request.body.cancel().catch(() => undefined);
    }
    return "too-large";
  }
  const reader = request.body?.getReader();
  if (reader === undefined) {
    return new Uint8Array();
  }
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (value === undefined || value.byteLength === 0) continue;
      total += value.byteLength;
      if (total > PROXY_BODY_MAX_BYTES) {
        await reader.cancel().catch(() => undefined);
        return "too-large";
      }
      chunks.push(value);
    }
  } catch (error) {
    await reader.cancel().catch(() => undefined);
    throw error;
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return bytes;
}

function admitBody(raw: Uint8Array, model: "fake" | "openai"): ArrayBuffer | undefined {
  let parsed: unknown;
  try {
    parsed = JSON.parse(new TextDecoder().decode(raw));
  } catch {
    return undefined;
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return undefined;
  }
  return asArrayBuffer(new TextEncoder().encode(JSON.stringify({ ...parsed, model })));
}

export async function POST(request: Request): Promise<Response> {
  const raw = await readCappedBody(request);
  if (raw === "too-large") {
    return jsonResponse(413, PAYLOAD_TOO_LARGE);
  }
  const body = admitBody(raw, pinnedModel(process.env.ASK_MODEL));
  if (body === undefined) {
    return jsonResponse(400, INVALID_BODY);
  }
  const origin = process.env.CAPITALCITE_API_ORIGIN ?? DEFAULT_ORIGIN;
  try {
    const upstream = await fetch(new URL("/ask/stream", origin), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body,
      signal: request.signal,
    });
    const headers = new Headers();
    const contentType = upstream.headers.get("content-type");
    if (contentType !== null) headers.set("content-type", contentType);
    return new Response(upstream.body, { status: upstream.status, headers });
  } catch (error) {
    if (request.signal.aborted) throw error;
    return jsonResponse(502, UPSTREAM_UNAVAILABLE);
  }
}

export function GET(): Response {
  return new Response(null, { status: 405 });
}
