"""Adapter from OpenAI Chat Completions tool calling to run_advisor's call_model."""


def make_openai_caller(model, temperature=0):
    """Return call_model(messages, tools). Needs OPENAI_API_KEY in the env.

    temperature=0 for the same reason as the cover letter agent: repeated
    runs on the same question should take the same path. gpt-5* models only
    accept the default temperature, so it is left out for them.
    """
    from openai import OpenAI

    client = OpenAI()
    extra = {} if model.startswith("gpt-5") else {"temperature": temperature}

    def call_model(messages, tools):
        response = client.chat.completions.create(
            model=model, messages=messages, tools=tools, **extra
        )
        message = response.choices[0].message
        return {
            "content": message.content,
            "tool_calls": [
                {"id": c.id, "name": c.function.name, "arguments": c.function.arguments}
                for c in (message.tool_calls or [])
            ],
        }

    return call_model
