# 0008. Plan-then-act agent

Date: 2026-10-16

Status: Accepted

Issue #8 asks for a planner → tool executor → synthesizer graph that can
compare a metric across banks and years, expose the plan, and stop when a
step or token budget is hit. Issue #7 already returns a grounded answer or a
refusal. This record is why the graph reuses that path instead of answering
on its own, why LangGraph owns the loop and not the model, and why numeric
grounding became unit-aware.

## The synthesizer is still ask()

A final answer is still `Answered` or `Refused`. The model still names a
passage index and a quote. `ground()` still copies chunk identity and still
refuses a quote that is not a verbatim substring, a trivial quote, or a
figure that is not in the cited quotes. `retrieval_refusal()` still runs
before the synthesizer sees the hits.

The agent does not get a second way to emit an answer. The synthesize node
calls `ask()` with the hits the tools accumulated. A hallucinated figure
after four successful searches is still a refusal. A named bank that was
never retrieved is still a refusal. Issue #9 can wrap `ToolRegistry.execute`
later without teaching the synthesizer a new contract.

## Plan, then act, then synthesize

The graph is plan → act* → synthesize, compiled with LangGraph `StateGraph`.
The planner returns either a list of tool calls or `synthesize`. The act
node runs one call at a time through `execute()`. Unknown tool names return
a structured error. They do not raise. After the planned calls finish, the
graph synthesizes. It does not keep chatting.

That is plan-then-act, not ReAct. A comparison such as JPMorgan vs Bank of
America at year-end 2024 is one plan with two `search_filings` calls, then
one grounded draft. The HTTP body includes `plan` and `traces` so a caller
can see those steps. `path=rag` stays the one-shot retrieve from #7, with
empty `plan` and `traces`.

Step and token budgets are checked at every node. Exceeding either one
writes `Refused(refusal_reason="step budget exceeded")` or
`token budget exceeded` and skips remaining calls. The graph does not guess
an answer to burn the last token. LangGraph `recursion_limit` is a backstop
above `ASK_MAX_STEPS`.

## LangGraph for the loop, not a second model SDK

#7 already speaks JSON over httpx. `StructuredModel` and
`FakeStructuredModel` stay. PydanticAI would own tools and structured
output and would replace that adapter. The project test that forbids a
`pydantic-ai` dependency stays.

LangGraph owns control flow: nodes, edges, and the recursion limit. Nodes
call our planner protocol and `ask()`. `create_react_agent` is not used.
LangGraph types stay in `capitalcite.agent.graph`. `capitalcite.ask` still
must not import fastapi, qdrant, or langgraph.

A hand-rolled `while` loop would have been smaller. The issue and the
README architecture name LangGraph or PydanticAI. LangGraph is the one that
adds a loop without taking the model.

## Four tools, one registry

`search_filings` wraps `retrieve()`. `calculator` is AST arithmetic with
Decimal. `xbrl_fact` reads SEC companyfacts. `fdic_bankfind` reads the FDIC
institutions API. Both HTTP tools take an injected JSON client.
`FakeXbrlClient` and `FakeFdicClient` keep pytest offline. `ASK_MODEL=fake`
does not dial those APIs.

The fake planner searches each named ticker and year from the question,
then the fake synthesizer quotes one hit per named entity so `ground()` and
`guard` can pass when the index actually holds those filings.

## Unit-aware figures

10-K tables often print millions as `21,338`. An answer that says
`$21.3 billion` is the same quantity, rounded. `ground()` now treats
thousand / million / billion as scale. A bare integer with at least four
digits may match an answer that carries a scale word when the scaled value
rounds to the answer's displayed precision. `$22 billion` does not round
from `21,338` million. Percentages do not take a scale. RAG and the agent
share this check because both end in `ground()`.

## What this leaves out

The fail-closed policy file, the audit log, and Langfuse spans are issues
#9–#11. RAGAS answer-quality cells stay TBD until a live LLM eval writes
them. Live `path=agent` with `model=openai` is UNVERIFIED when
`OPENAI_API_KEY` is empty, the same rule as #7.
