"""Compact JSON printer for the README terminal recording."""

from __future__ import annotations

import json
import sys


def main() -> None:
    kind = sys.argv[1]
    body = json.load(sys.stdin)
    if kind == "health":
        print(json.dumps(body, sort_keys=True))
        return
    if kind == "search":
        hits = body.get("hits") or []
        print(f"points={body.get('point_count')} hits={len(hits)} mode=hybrid")
        for i, hit in enumerate(hits, start=1):
            chunk = hit.get("chunk") or {}
            text = " ".join(str(chunk.get("text") or "").split())
            if len(text) > 90:
                text = text[:87] + "..."
            print(
                f"  {i}. score={hit.get('score'):.4f} "
                f"{chunk.get('ticker')} {chunk.get('section')} {text}"
            )
        return
    if kind == "ask":
        cites = body.get("citations") or []
        answer = " ".join(str(body.get("answer") or "").split())
        if len(answer) > 140:
            answer = answer[:137] + "..."
        print(
            f"kind={body.get('kind')} path={body.get('path')} "
            f"citations={len(cites)} score={body.get('retrieval_score')}"
        )
        if answer:
            print(f"  answer: {answer}")
        plan = body.get("plan") or []
        if plan:
            tools = ", ".join(
                str(step.get("tool")) for step in plan if isinstance(step, dict)
            )
            print(f"  plan: {tools} steps_used={body.get('steps_used')}")
        return
    print(json.dumps(body, sort_keys=True))


if __name__ == "__main__":
    main()
