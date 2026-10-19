"""The advisor agent loop.

The model chooses which tool to call next, sees the result, and keeps going
until it calls final_answer or hits max_steps. The loop never writes: any
change the model wants comes back as `proposed_changes` for the user to
confirm (see advisor/apply.py).
"""

import json
import time
from dataclasses import dataclass, field

from advisor.tools import TOOLS, dispatch
from advisor.tracing import traceable

# One traced runner per tool, so each call shows in the trace under its own name.
_TOOL_RUNNERS = {
    t["function"]["name"]: traceable(name=t["function"]["name"], run_type="tool")(dispatch)
    for t in TOOLS
}

SYSTEM_PROMPT = """You are the savings advisor inside SaWings.
The user asks a what-if question about their Income, Obligations and Goals.

Rules:
- Call get_finances first.
- Never calculate Installments, Surplus or dates yourself. Use simulate_plan
  and quote only numbers it returned.
- Try more than one option when the first does not fit (a later Deadline,
  a smaller amount or a different Importance for what was asked) and
  compare them.
- Always say which existing Goals a change would make Unreachable and which
  Top-ups it pauses.
- You cannot save anything. Put the changes you recommend in
  final_answer.proposed_changes; the user confirms them.
- Only propose changes to what the user asked about. Never change the
  Deadline or Importance of a Goal the user did not mention. Other options,
  like dropping a Nice-to-have Goal, go in the summary, not in
  proposed_changes.
- Never propose an Income or Obligation change the user did not mention.
- If the question is not about Income, Obligations or Goals, answer briefly
  and propose no changes.
- Use these words exactly: Income, Obligation, Goal, Deadline, Importance,
  Installment, Surplus, Unreachable Goal, Top-up, Plan.
- Write the summary for the user in plain words, never field or tool
  names: "a target amount of 800 €", not "target_amount 800".
- Finish by calling final_answer exactly once."""


@dataclass
class AdvisorResult:
    verdict: str | None
    summary: str
    proposed_changes: list
    stopped_reason: str  # "final_answer" | "max_steps"
    trace: list = field(default_factory=list)

    @property
    def tool_calls(self):
        return sum(1 for t in self.trace if t["type"] == "tool")


@traceable(name="advisor", run_type="chain")
def run_advisor(question, snapshot, call_model, today, max_steps=8):
    """Answer `question` about `snapshot`.

    call_model(messages, tools) -> {"content": str | None,
                                    "tool_calls": [{"id", "name", "arguments"}]}
    Inject a fake in tests; use advisor.openai_client in the app.
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    trace = []

    for step in range(max_steps):
        started = time.perf_counter()
        reply = call_model(messages, TOOLS)
        trace.append({"type": "model", "step": step, "content": reply.get("content"),
                      "ms": round((time.perf_counter() - started) * 1000)})
        calls = reply.get("tool_calls") or []
        messages.append({
            "role": "assistant",
            "content": reply.get("content"),
            **({"tool_calls": [
                {"id": c["id"], "type": "function",
                 "function": {"name": c["name"], "arguments": c["arguments"]}}
                for c in calls
            ]} if calls else {}),
        })

        if not calls:
            messages.append({"role": "user", "content": "Finish by calling final_answer."})
            continue

        for call in calls:
            runner = _TOOL_RUNNERS.get(call["name"], dispatch)
            result, is_final = runner(call["name"], call["arguments"], snapshot, today, question)
            trace.append({"type": "tool", "step": step, "name": call["name"],
                          "arguments": call["arguments"], "result": result})
            if is_final:
                return AdvisorResult(result["verdict"], result["summary"],
                                     result["proposed_changes"], "final_answer", trace)
            messages.append({"role": "tool", "tool_call_id": call["id"],
                             "content": json.dumps(result)})

    return AdvisorResult(None, "I could not finish within the step limit.", [], "max_steps", trace)
