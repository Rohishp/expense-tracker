# Observability: tracing and evaluating SaWings's agents

Two kinds of agents, two LangSmith projects, one account.

| Project | What it traces | How |
|---|---|---|
| `sawings-app` | The app's LLM code: the advisor (OpenAI) and Goal suggestions (Anthropic), in local runs and evals | `@traceable` + wrapped clients (`advisor/tracing.py`) |
| `sawings-build` | The Claude Code workflow that builds the app: `/create-spec`, `/test-feature`, `/code-review-feature`, `/ship-feature` and the four subagents | LangSmith's Claude Code plugin |

## 1. One-time setup

1. Create a LangSmith account at smith.langchain.com and an API key (Settings > API Keys).
2. `pip install -r requirements.txt` (now includes `langsmith`).
3. `cp .env.example .env` and fill in the keys. `.env` is gitignored.

## 2. Trace the app (`sawings-app`)

In `.env`:

```
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_...
LANGSMITH_PROJECT=sawings-app
```

Then any of these sends traces:

```bash
python -m advisor.evals --model gpt-5-nano --runs 3 --workers 6
python -m suggestions_evals --runs 3
python app.py        # click "Suggest Importance and Dependencies" on a Goal
```

What one advisor run looks like:

```
advisor (chain)                 metadata: scenario, run, model · tag: eval
├── ChatOpenAI (llm)            prompt, tool calls chosen, tokens, latency
├── get_finances (tool)
│   └── planner.plan
├── ChatOpenAI (llm)
├── simulate_plan (tool)        the changes the model tried + the diff it got
│   └── planner.plan ×2
└── final_answer (tool)
```

A suggestion is `suggest_goal (chain) > ChatAnthropic (llm)`.

**Where to look in LangSmith**
- **Runs list:** filter `metadata.scenario = licence_sooner` to put the k runs of one task side by side and see where they took different paths.
- **Tag `eval`:** keeps eval traffic apart from your manual clicks.
- **Monitoring tab:** latency, token, cost and error-rate charts per project, over time.
- **Compare models:** run the evals with two `--model` values. `metadata.model` splits them.

Workflow: run evals → `evals/results/runs.csv` gets the scores and `evals/results/<eval>/` the traces → open the failed scenario in LangSmith to see *why* it failed → fix the prompt or the task → rerun.

## 3. Trace the Claude Code workflow (`sawings-build`)

Needs Node.js 20 or newer (except on a Mac). Inside Claude Code, in this repo:

```
/plugin marketplace add langchain-ai/langsmith-claude-code-plugins
/plugin install langsmith-tracing@langsmith-claude-code-plugins
/reload-plugins
```

The install enables the plugin in the committed `.claude/settings.json`, but it sends nothing until tracing is switched on. To switch it on for this repo only, `cp .claude/langsmith.json.example .claude/langsmith.json` and put in your LangSmith key, the same one as in `.env` (the file is gitignored; `~/.claude/langsmith.json` would trace every project). Start a new session, send a message, then run `/langsmith-tracing:trace` for a link to the conversation's trace. To keep one conversation out of LangSmith, run `/langsmith-tracing:mute` before sending anything sensitive.

Every Claude Code turn becomes a trace: your message, the model calls, every tool call (Read, Edit, Bash, GitHub MCP) and subagent runs nested underneath. Turns of one session are grouped in the **Threads** tab.

What to look at:
- `/code-review-feature`: both reviewer subagents as sibling runs, with their findings.
- `/test-feature`: the test-writer's run, then the test-runner's pytest output.
- Tokens and time per feature: the cost of building step N through the pipeline.

Limits: a subagent appears only once it finishes (an interrupted one is lost), system prompts are not captured, and prompts plus file contents are uploaded with best-effort secret redaction only. Keep the project private.

**Vendor-neutral alternative:** Claude Code's built-in OpenTelemetry (`CLAUDE_CODE_ENABLE_TELEMETRY=1`). Metrics and events are generally available; full traces need `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1`. Point it at a local Phoenix or Jaeger. See https://code.claude.com/docs/en/monitoring-usage.

## 4. Rules

- **Never set `LANGSMITH_TRACING` on Railway.** The privacy page names Anthropic and OpenAI as the only third parties that see Goal data; tracing production would send that data to LangSmith too. Production is safe by construction: only `python app.py` loads `.env`, and gunicorn does not.
- **Tests never trace.** `tests/conftest.py` forces `LANGSMITH_TRACING=false`.
- **Tracing is optional.** Without `langsmith` installed, or with the flag off, the code behaves exactly as before.

## 5. Next: evaluate the build agents

Tracing shows what the subagents did; it does not show whether they are good. The planned next step is planted-bug evals: branches with known bugs (f-string SQL, a missing `user_id` filter, a missing `@csrf_protect`) run headlessly through `/code-review-feature` with `claude -p --output-format stream-json`, scored as bugs caught out of bugs planted.
