"""Optional LangSmith tracing for the app's LLM code (advisor and suggestions).

Sends nothing unless `langsmith` is installed AND LANGSMITH_TRACING=true.
Keep it off in production: the privacy page names Anthropic and OpenAI as
the only third parties that see Goal data (see docs/observability.md).
"""

try:
    from langsmith import traceable

    ENABLED = True
except ImportError:  # tracing is optional
    ENABLED = False

    def traceable(*args, **kwargs):
        if len(args) == 1 and callable(args[0]) and not kwargs:
            return args[0]
        return lambda fn: fn


def run_extra(metadata=None, tags=None):
    """Keyword args that tag one traced call, e.g. with the eval scenario id."""
    if not ENABLED:
        return {}
    return {"langsmith_extra": {"metadata": metadata or {}, "tags": tags or []}}


def wrap_client(client):
    """Wrap an OpenAI or Anthropic client so each model call is logged."""
    if not ENABLED:
        return client
    from langsmith import wrappers

    if type(client).__module__.startswith("anthropic"):
        return wrappers.wrap_anthropic(client)
    return wrappers.wrap_openai(client)
