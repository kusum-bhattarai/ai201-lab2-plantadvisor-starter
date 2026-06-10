import json
from groq import Groq
from config import GROQ_API_KEY, LLM_MODEL, MAX_TOOL_ROUNDS
from tools import lookup_plant, get_seasonal_conditions

_client = Groq(api_key=GROQ_API_KEY)

# ──────────────────────────────────────────────
# Tool definitions
#
# These are the schemas that tell the LLM what tools are available and how to
# call them. The LLM reads these descriptions and decides when (and how) to use
# each tool. They're already complete — your job is to implement the tool
# functions in tools.py and the agent loop below.
# ──────────────────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "lookup_plant",
            "description": (
                "Look up care information for a specific houseplant by name. "
                "Returns detailed watering, light, humidity, and temperature requirements. "
                "Use this whenever the user asks about a specific plant."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "plant_name": {
                        "type": "string",
                        "description": "The plant name to look up. Can be a common name, scientific name, or nickname (e.g., 'pothos', 'devil's ivy', 'Monstera deliciosa').",
                    }
                },
                "required": ["plant_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_seasonal_conditions",
            "description": (
                "Get seasonal care adjustments for houseplants. "
                "Returns guidance on watering, fertilizing, light, and pests for the current or specified season. "
                "Use this when a user asks a season-specific question, or to complement plant care advice with seasonal context."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "season": {
                        "type": "string",
                        "description": "The season to get care conditions for. If omitted, the current season is detected automatically.",
                        "enum": ["spring", "summer", "fall", "winter"],
                    }
                },
                "required": [],
            },
        },
    },
]

# ──────────────────────────────────────────────
# System prompt
# ──────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are a knowledgeable and friendly plant care advisor. "
    "Help users care for their houseplants by looking up specific plant information "
    "and current seasonal conditions using your available tools.\n\n"
    "Always use your tools to look up plant-specific information before answering — "
    "don't rely on your general knowledge alone. If a plant isn't in your database, "
    "say so clearly and offer general guidance based on what the user describes.\n\n"
    "Keep your advice practical and specific. Cite the source of your information "
    "when you have it (e.g., 'According to the care data for your monstera...')."
)

# ──────────────────────────────────────────────
# Tool dispatch
#
# This is already complete. It routes tool calls from the LLM to the actual
# Python functions in tools.py, and returns results as JSON strings (which is
# what the Groq API expects for tool results).
# ──────────────────────────────────────────────

def dispatch_tool(tool_name: str, tool_args: dict) -> str:
    """Route a tool call to the correct function and return the result as a JSON string."""
    print(f"  → Tool call: {tool_name}({tool_args})")
    if tool_name == "lookup_plant":
        result = lookup_plant(tool_args["plant_name"])
    elif tool_name == "get_seasonal_conditions":
        result = get_seasonal_conditions(tool_args.get("season"))
    else:
        result = {"error": f"Unknown tool: {tool_name}"}
    print(f"  ← Result: {json.dumps(result)[:120]}{'...' if len(json.dumps(result)) > 120 else ''}")
    return json.dumps(result)


# ──────────────────────────────────────────────
# Agent loop
# ──────────────────────────────────────────────

def _create_completion(messages: list, use_tools: bool = True):
    """
    Call the Groq chat completions API, retrying on the transient
    `tool_use_failed` error.

    Llama-3.3 on Groq intermittently emits a tool call as malformed text
    (e.g. `<function=lookup_plant({...})</function>`) instead of a structured
    tool_call, which the API rejects with a 400 `tool_use_failed`. It's flaky,
    not deterministic — a retry almost always succeeds. We retry only that
    specific error and let everything else propagate.
    """
    kwargs = {"model": LLM_MODEL, "messages": messages}
    if use_tools:
        kwargs["tools"] = TOOL_DEFINITIONS
        kwargs["tool_choice"] = "auto"

    last_error = None
    for _ in range(3):
        try:
            return _client.chat.completions.create(**kwargs)
        except Exception as e:
            if "tool_use_failed" not in str(e):
                raise
            last_error = e
            print("  ↻ Transient tool_use_failed from the model — retrying.")
    raise last_error


def run_agent(user_message: str, history: list) -> str:
    """
    Run the plant care agent for one user turn and return its response.

    TODO — Milestone 2:

    The agent loop follows a specific pattern that you'll implement here. Read
    specs/agent-loop-spec.md carefully before writing any code — understand the
    full loop before implementing any part of it.

    The loop works like this:
      1. Build a messages list: system prompt + conversation history + new user message
      2. Call the LLM with messages and TOOL_DEFINITIONS
      3. If the response contains tool_calls:
           a. Append the assistant message (with tool_calls) to messages
           b. For each tool call: execute via dispatch_tool(), append the result
           c. Call the LLM again with the updated messages
           d. Repeat until no more tool_calls (or MAX_TOOL_ROUNDS is reached)
      4. Return the final text response

    Key details to get right:
      - The assistant message must be appended BEFORE tool results
      - Tool result messages use role="tool" with a tool_call_id field
      - Append the assistant's message object directly (not just its content)
      - The history format from Gradio: list of [user_message, assistant_message] pairs

    Before writing code, complete specs/agent-loop-spec.md.
    """
    FALLBACK = (
        "Sorry — I ran into a problem putting together an answer. "
        "Could you try rephrasing your question?"
    )

    try:
        # 1. Build the messages list: system prompt + replayed history + new message.
        # Gradio 6 hands history as "messages" format ({"role", "content"} dicts);
        # older Gradio uses "tuples" format ([user_msg, assistant_msg] pairs).
        # Handle both so the loop is correct across turns regardless of version.
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for entry in history:
            if isinstance(entry, dict):
                # Messages format — already API-shaped; keep user/assistant turns.
                if entry.get("role") in ("user", "assistant") and entry.get("content"):
                    messages.append({"role": entry["role"], "content": entry["content"]})
            else:
                # Tuples format — [user_msg, assistant_msg].
                user_msg, assistant_msg = entry
                messages.append({"role": "user", "content": user_msg})
                if assistant_msg:
                    messages.append({"role": "assistant", "content": assistant_msg})
        messages.append({"role": "user", "content": user_message})

        # 2. Tool-calling loop, bounded by MAX_TOOL_ROUNDS so it can never run forever.
        for _ in range(MAX_TOOL_ROUNDS):
            response = _create_completion(messages, use_tools=True)
            assistant_message = response.choices[0].message

            # Exit (a): no tool calls means the LLM has its final answer.
            if not assistant_message.tool_calls:
                return assistant_message.content or FALLBACK

            # Tool calls requested: the assistant message MUST be appended first,
            # then one tool result per call, each linked back by tool_call_id.
            messages.append(assistant_message)
            for tool_call in assistant_message.tool_calls:
                tool_name = tool_call.function.name
                # Guard the arguments: a no-arg tool call can arrive as "",
                # "null", or "{}". Coerce all of those to an empty dict so
                # dispatch_tool always gets a dict to call .get() on.
                tool_args = json.loads(tool_call.function.arguments or "{}") or {}
                tool_result = dispatch_tool(tool_name, tool_args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result,
                })

        # Exit (b): MAX_TOOL_ROUNDS reached and the agent is still calling tools.
        # Make one final call with no tools to force a text answer from what we have.
        final_response = _create_completion(messages, use_tools=False)
        return final_response.choices[0].message.content or FALLBACK

    except Exception as e:
        print(f"  ⚠️ Agent error: {e}")
        return FALLBACK
