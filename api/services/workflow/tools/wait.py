from typing import Any, Dict

def get_wait_tools() -> list[Dict[str, Any]]:
    """Get wait tool definitions for LLM function calling."""
    return [
        {
            "type": "function",
            "function": {
                "name": "wait_for_user",
                "description": (
            "Wait for the user to return. Use this when the user explicitly asks you to wait or hold on. "
            "You MUST specify the duration in seconds to wait. "
            "If the user does not specify a duration, default to 60 seconds. "
            "NOTE: The minimum wait time is 15 seconds. If the user says 'give me a second' or 'wait a sec', "
            "they mean at least 20 seconds. Do not use this tool for very short pauses (< 15s)."
        ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "seconds": {
                            "type": "integer",
                            "description": "The number of seconds to wait. Defaults to 60 if not specified.",
                        },
                        "message": {
                            "type": "string",
                            "description": "A short conversational acknowledgment to speak to the user before waiting (e.g. 'Sure, I will wait.').",
                        }
                    },
                    "required": ["seconds", "message"],
                },
            },
        }
    ]
