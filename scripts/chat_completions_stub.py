from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main() -> None:
    log_path = Path(sys.argv[1])

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw.decode("utf-8")) if raw else None
            except json.JSONDecodeError:
                body = None
            model = body.get("model") if isinstance(body, dict) else None
            record = {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "model": model,
            }
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
            content = json.dumps(
                {
                    "kind": "refused",
                    "refusal_reason": "stub local model",
                    "citations": [],
                }
            )
            payload = json.dumps(
                {
                    "choices": [{"message": {"content": content}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, template: str, *args: object) -> None:
            _ = template, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    print(server.server_address[1], flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
