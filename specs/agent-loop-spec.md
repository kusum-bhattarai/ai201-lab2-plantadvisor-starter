# Spec: `run_agent()`

**File:** `agent.py`
**Status:** Partially pre-filled — complete the two blank fields before implementing

---

## Purpose

Orchestrate a single conversational turn for the Plant Advisor agent. Given a user message and the conversation history, call the LLM with available tools, execute any tool calls the LLM requests, and return the final text response.

This is the core of what makes Plant Advisor an *agent* rather than a simple chatbot: the ability to decide which tools to call, use their results to inform its response, and loop until it has everything it needs.

---

## Input / Output Contract

**Inputs:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `user_message` | `str` | The user's current message |
| `history` | `list` | Gradio conversation history — list of `[user_msg, assistant_msg]` pairs |

**Output:** `str`

The agent's final text response for this turn. Should never be empty — if something goes wrong, return a user-readable fallback message.

---

## Design Decisions

*Read `specs/system-design.md` (especially the "How the Groq Tool Calling API Works" section) before reviewing these. Complete the two blank fields before writing any code.*

---

### Messages list structure

The messages list must start with the system prompt, then replay the conversation
history, then add the new user message. Gradio history is a list of `[user, assistant]`
pairs — convert each pair to two API-format dicts:

```python
messages = [{"role": "system", "content": SYSTEM_PROMPT}]

for user_msg, assistant_msg in history:
    messages.append({"role": "user", "content": user_msg})
    if assistant_msg:
        messages.append({"role": "assistant", "content": assistant_msg})

messages.append({"role": "user", "content": user_message})
```

---

### Initial LLM call

Pass the model, the messages list, the tool definitions, and `tool_choice="auto"`
so the LLM can decide whether to call a tool or respond directly:

```python
response = client.chat.completions.create(
    model=LLM_MODEL,
    messages=messages,
    tools=TOOL_DEFINITIONS,
    tool_choice="auto",
)
```

---

### Detecting tool calls in the response

The response object has a `choices` list. Index 0 gives the assistant message.
Check its `tool_calls` attribute — if it's truthy, the LLM wants to call tools:

```python
assistant_message = response.choices[0].message

if not assistant_message.tool_calls:
    # No tool calls — LLM has a final answer
    ...
```

---

### Appending the assistant message

When there are tool calls, append the full assistant message object to `messages`
**before** appending any tool results. The API requires this ordering — a tool
result message must immediately follow the assistant message that requested it:

```python
messages.append(assistant_message)  # must come first
```

---

### Executing and appending tool results

For each tool call, extract the name and arguments, call `dispatch_tool()`, and
append the result as a `"tool"` role message. The `tool_call_id` links this result
back to the specific tool call that requested it:

```python
for tool_call in assistant_message.tool_calls:
    tool_name = tool_call.function.name
    tool_args = json.loads(tool_call.function.arguments)
    tool_result = dispatch_tool(tool_name, tool_args)

    messages.append({
        "role": "tool",
        "tool_call_id": tool_call.id,
        "content": tool_result,
    })
```

---

### Loop termination conditions

The loop body runs at most `MAX_TOOL_ROUNDS` times — `for _ in range(MAX_TOOL_ROUNDS)`.
The bounded counter is itself the guard against looping forever: even if the LLM keeps
requesting tools (e.g. a tool returns an empty/unhelpful result and it retries), the
loop cannot exceed the cap.

(a) **No tool calls — normal exit.** After each LLM call, inspect
`response.choices[0].message`. If `assistant_message.tool_calls` is falsy, the LLM has
a final answer. Return `assistant_message.content or FALLBACK` — the `or` guard means a
rare `None`/empty content never returns an empty string.

(b) **MAX_TOOL_ROUNDS reached — graceful exit.** If the `for` loop completes without
ever hitting the no-tool-calls branch, the agent is still asking for tools. Rather than
return a stub or crash, make ONE final LLM call with no tools attached, forcing a text
answer from the context already gathered, and return `content or FALLBACK`.

**Failure modes handled:**
- *Loop forever* → bounded `range(MAX_TOOL_ROUNDS)`.
- *Returns empty string* → every return path uses `... or FALLBACK`; the post-loop call
  also forces a text response so the cap-hit case isn't empty either.
- *Raises an exception* (API error, `json.loads` on malformed tool arguments) → the whole
  body is wrapped in try/except that logs and returns FALLBACK, honoring the contract that
  the function never returns empty.

---

### Extracting the final text response

The final text lives at `response.choices[0].message.content` — a `str`. Walk it down:
`response.choices` is a list of completion choices; index `[0]` is the first (and, with
default settings, only) one; `.message` is the assistant message object; `.content` is
its text. This is the same `assistant_message` we already bound to check `.tool_calls`,
so the access is just `assistant_message.content`.

Guard it with `or FALLBACK` when returning, because `content` is `None` whenever the
assistant message carried `tool_calls` instead of text — on the no-tool-calls exit it
will be a real string, but the guard keeps an unexpected `None`/`""` from ever being
returned to the user.

```python
return assistant_message.content or FALLBACK
```

---

## Implementation Notes

*Fill this in after implementing and testing.*

**Trace of a working agent turn (what tools were called and in what order):**

```
Query: "How should I water my monstera this time of year?" (checkpoint query)
Round 1 tool calls (same assistant message, executed in order):
  1. lookup_plant({"plant_name": "monstera"})        -> found: True, full Monstera entry
  2. get_seasonal_conditions({})                      -> Summer (auto-detected, June)
Round 2: assistant message has no tool_calls -> loop exits, return content.
Final response: Cites the monstera's specific watering (water when top 2 inches of
  soil are dry, ~every 1–2 weeks) AND connects it to summer (water more frequently,
  check soil every few days, watch for heat stress). Two tools, one round.

Note: a plain "How do I care for my pothos?" calls only lookup_plant — the agent
correctly skips the season tool when the question isn't season-specific.
```

**What happens when you ask about a plant that isn't in the database?**

```
Asked "How should I care for my venus flytrap?". The agent calls
lookup_plant({"plant_name": "Venus flytrap"}), which returns
{"found": false, "name": "venus flytrap", "message": "...not found... database
currently covers: <list>... offer general guidance, making clear it's general..."}.
The LLM reads that message and responds honestly: states the venus flytrap isn't in
the curated database, then offers general carnivorous-plant guidance. The not-found
message did its job — it steered the agent's behavior, exactly as designed in the
tool-functions spec.
```

**One thing about the tool call API that surprised you:**

```
Two things. (1) A no-argument tool call doesn't always arrive as "{}" — Groq sent
get_seasonal_conditions's arguments as the JSON literal "null", so json.loads()
returned None and dispatch_tool crashed on None.get("season"). I had to coerce args
with `json.loads(arguments or "{}") or {}`. The loop owns argument robustness, not
just the tools. (2) Llama-3.3 on Groq intermittently emits a tool call as malformed
TEXT (<function=lookup_plant({...})</function>) instead of a structured tool_call,
which the API rejects with a 400 tool_use_failed. It's non-deterministic — the same
prompt fails ~half the time and succeeds on retry — so I added a targeted retry on
that specific error to keep the app usable.
```
