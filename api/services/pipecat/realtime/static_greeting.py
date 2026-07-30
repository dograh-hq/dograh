def format_static_greeting_prompt(greeting_text: str) -> str:
    return (
        "The phone call has just connected. Greet the caller now: "
        "say the following opening line out loud, exactly as written, "
        "in a natural spoken voice, and then stop and wait for the "
        "caller to respond. Do not add anything before or after it.\n\n"
        f'"{greeting_text}"'
    )


def format_say_verbatim_prompt(text: str) -> str:
    """Instruction for a mid-call fixed message (e.g. an end-call goodbye or a
    node-transition line): say the exact text and nothing else. Unlike the
    greeting prompt, it makes no assumptions about the call having just
    connected."""
    return (
        "Say the following line out loud, exactly as written, in a natural "
        "spoken voice. Do not add anything before or after it.\n\n"
        f'"{text}"'
    )
