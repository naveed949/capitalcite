# 0009. Fail-closed tool gate

Date: 2026-10-16

Status: Accepted

Issue #9 asks for a policy file that allow-lists tools, argument schemas,
and per-tool budgets. Anything else is a deny. A deny is a structured
error the agent can record in its trace. This record is why the gate
wraps `execute`, why a broken policy file denies every name, and why
default-allow cannot load.

## The gate is the only way to run a tool

ADR 0008 already said unknown names return `ToolResult(ok=False)` and
that #9 can wrap `ToolRegistry.execute`. The graph still calls one
method. `Gate` implements the same `ToolExecutor` protocol. An allow
forwards to the registry. A deny never calls the runner.

`path=rag` does not construct a gate. Retrieval is not a tool call.

The planner is untrusted. `ToolCall.name` is a string. The policy is
the allow-list, not `TOOL_NAMES` in Python. The shipped file happens to
name the four registered tools. A test fails if those sets drift.

## Policy is data. Load failure is deny-all.

The shipped file is `src/capitalcite/gate/tools.json`. `GATE_POLICY`
points at a replacement. `load_policy` never raises. A missing file,
invalid JSON, extra keys, an unsupported schema keyword, or
`default` other than `deny` becomes `BrokenPolicy`. Every later
`decide()` is `policy_load_failed`.

That is different from an empty allow-list. An empty `tools` map that
still says `default: deny` is a valid policy. Those denies are
`unknown_tool`. A file we could not parse is not treated as "no tools
on purpose."

A default-allow file does not load. Fail-closed is not a flag.

Argument schemas live in the file as a JSON Schema subset: `type`,
`properties`, `required`, `additionalProperties`, `minLength`,
`minimum`, `maximum`. An unknown keyword fails at load, not at
decide. Extra keys on a call are `invalid_args`.

`max_calls` and `max_arg_bytes` are per request. `_run_agent`
constructs a new `Gate` each time. Process-wide counters would be
shared mutable state across requests. They are not used.

Graph step and token budgets still stop the loop. They are not an
allow-list.

## Injection in a retrieved doc cannot run a denied tool

The official planner prompt does not include chunk text. A live model
still might be steered if a later change shows passages to the planner,
or if the model invents a tool name. The gate does not care why the
name was requested.

The red-team suite uses a hostile planner that searches, then follows
`CALL_TOOL` instructions inside hit text. `shell` is denied. Extra
keys on `calculator` are denied. The inner runner is not called. The
default fake planner, given the same injected hit, only calls
`search_filings`.

#10 can wrap the same `execute` and write one record per decision.
#11 can span `decide`. This change does not add an audit log or a
Langfuse client. `ToolResult.reason` is the machine-readable deny
code those wrappers copy. Oversized arguments are `args_too_large`.
Call-count overage stays `budget_exceeded`.

`Gate.execute` catches any exception from `decide()` and returns
`ok=False`, `detail="gate error"`, `reason="gate_error"`. `_deny`
stringifies keys and stores `repr` when args are not a mapping or
cannot be walked, so building a deny cannot raise. Odd inputs
(non-string keys, cycles, a non-dict) still never call the runner.

## CLI is the user path

`capitalcite gate decide` prints one JSON decision and does not run
the tool. `validate` loads the file. `redteam` runs the injection
suite. `bench` times `decide()` with no network. Pytest is not
verification.

## Alternatives

Building the registry from the policy file would drop tools that the
policy omitted. A policy-load failure and an empty allow-list would
look the same. The wrapper keeps those reasons apart.

A Rego or Cedar engine would hide more policy language behind one
call. It would also add a runtime and a second schema for four tools.

Checking the allow-list inside `act_node` would leave
`ToolRegistry.execute` as a second ungated path. The next caller that
imports the registry would skip the gate.

JSON Schema in the file, not a second copy of the pydantic models, is
what an operator edits. The registry still validates on allow. Two
layers, one authority for "is this call permitted."
