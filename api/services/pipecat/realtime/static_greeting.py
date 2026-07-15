def format_static_greeting_prompt(greeting_text: str) -> str:
    return (
        "The phone call has just connected. Greet the caller now: "
        "say the following opening line out loud, exactly as written, "
        "in a natural spoken voice, and then stop and wait for the "
        "caller to respond. Do not add anything before or after it.\n\n"
        f'"{greeting_text}"'
    )
