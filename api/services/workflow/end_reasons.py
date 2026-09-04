"""End-of-call reasons the engine stamps that ``pipecat.utils.enums.EndTaskReason`` lacks.

``EndTaskReason.VOICEMAIL_DETECTED`` means "the native detector recognised an
answering machine and the call ended there". This one means the configured
voicemail message was played to completion *first*. Consumers that treat a
voicemail as "nobody was reached" must not count this as undelivered, and a
dialer that retries voicemails should not redial a mailbox that already holds
the message — so the two reasons are deliberately distinct strings.
"""

VOICEMAIL_MESSAGE_LEFT = "voicemail_message_left"
