# Spec: Conversation Memory (plant tracking)

**Files:** `tools.py` (detection helper), `agent.py` (injection into the loop)
**Status:** New feature.

---

## Purpose

Make the agent remember which plants the user has mentioned earlier in the
conversation and proactively connect new questions to them. Concretely: if a user
asks about their pothos, then later asks a *general* watering question, the agent
should be able to say "Since you mentioned you have a pothos, …" instead of treating
each turn as isolated.

Today the full Gradio history is already replayed into the messages list, so the LLM
*can* see prior turns — but it has no explicit signal about which plants the user owns,
so it rarely makes the connection on its own. This feature surfaces that signal
deliberately.

---

## Design Decisions

### Where the memory lives — derive from history, not a stored object

The memory is **recomputed from `history` on every turn**, not held in a stateful
store. `run_agent(user_message, history)` already receives the complete per-session
conversation, so the set of previously mentioned plants is a pure function of that
history. This avoids:

- **Module-global state** — would leak one user's plants into another user's session.
- **`gr.State` plumbing** — would require threading a state object through `app.py`'s
  `chat()` and the `ChatInterface` wiring for no real gain.

Deriving per turn keeps the loop stateless and correct across concurrent sessions,
and it reuses the plant database already loaded in `tools.py`.

### How a mention is detected — word-boundary scan of the plant database

A "mention" is any database plant whose key, display name, or alias appears in a
user message. This is **substring-in-free-text** matching, which is different from
`lookup_plant()`'s exact-match-on-the-whole-string — here the plant name is embedded
in a sentence ("my pothos is drooping").

Matching uses a **word-boundary regex** (`\bname\b`) per candidate name, not a plain
`in` substring test, to avoid false positives. Plain substring matching would let a
short alias match the middle of an unrelated word; word boundaries require the name to
appear as its own token/phrase. Multi-word aliases ("mother-in-law's tongue", "swiss
cheese plant") are matched as full phrases, so a stray "fig" does not match "banjo fig".

Detection scans **user messages only** — these are what the user said they own. The
assistant's replies are excluded (they're derived, not the user's stated context), and
the current incoming `user_message` is excluded (a plant named in *this* turn is handled
by the normal tool call, not by "memory").

### How the memory is surfaced — a context note appended to the system message

When one or more plants are remembered, a short note is appended to the **system
message content for that turn only** (the messages list is rebuilt each call, so the
module-level `SYSTEM_PROMPT` constant is never mutated). The note lists the plants and
instructs the LLM to connect advice to them when relevant — and explicitly *not* to
force the connection when unrelated, so it doesn't shoehorn "your pothos" into every
answer. When no plants have been mentioned, no note is added and behavior is unchanged.

System message (not a user/assistant turn) is the right home because this is a
standing instruction about the user, not a conversational utterance.

---

## Input / Output Contract

`find_mentioned_plants(text: str) -> list[str]` — returns the **display names** of
every database plant referenced in `text`, de-duplicated, in database order. Empty list
if none.

`run_agent` behavior is unchanged except that, when history contains plant mentions,
the system message gains a memory note. Output type and all existing guarantees
(non-empty, fallback on error) are preserved.

---

## Implementation Notes

*Filled in after testing.*

**Trace: user mentions a plant, then asks a general follow-up.**

```
Turn 1: "How do I care for my pothos?"        -> lookup_plant(pothos)
Turn 2 (history has the pothos turn): "How often should I water, generally?"
  find_mentioned_plants over prior user turns -> ["Pothos"]
  Memory note appended to the system message: "plants mentioned: Pothos ..."
  Response: agent looked up pothos and answered with pothos-specific watering
  ("every 1-2 weeks, let the top inch dry"), treating the general question through
  the lens of the remembered plant. Connection made.
```

**Does it avoid forcing the connection when unrelated?**

```
Control: "What temperature is too cold for most houseplants?" with the same pothos
history. The agent answered the general temperature question first, then closed with
"Since you mentioned you have a pothos, be sure to keep it in a spot that maintains a
comfortable temperature..." — it connected because temperature genuinely applies to
the pothos, as a closing aside rather than hijacking the answer. The "don't force it"
instruction keeps the connection proportionate rather than shoehorned.
```

**One edge case discovered:**

```
The memory note is appended with `messages[0]["content"] += ...`. Because messages[0]
is a fresh dict built each turn and SYSTEM_PROMPT is an immutable str, the += rebinds
only this turn's dict value — the module-level SYSTEM_PROMPT constant is never mutated
and no note leaks into the next turn. Verified: SYSTEM_PROMPT is unchanged after a call
and contains no "Conversation memory" text. Detection also correctly ignores plants
NOT in the database (e.g. "bird of paradise" -> []), since memory is keyed to the
curated DB; out-of-DB plants are still handled by the not-found path, just not tracked.
```
