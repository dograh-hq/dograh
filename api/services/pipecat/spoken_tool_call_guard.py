"""Separate accidental tool serialization from streamed conversational text.

The pending buffer holds a lexical token or a structured fragment, never a second
sentence aggregation. Calls are candidates only; the response owner decides when
they may execute, after reconciling native calls and the completion outcome.

Spoken output also drops text that no listener should hear when it opens a
sentence: identifier tokens, stage directions, markdown structure and JSON
objects. Text chat keeps those, because a reader may legitimately receive them.
"""

import json
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from loguru import logger
from referencing import Registry
from referencing.exceptions import Unresolvable


@dataclass(frozen=True)
class RecoveredToolCall:
    name: str
    arguments: dict[str, Any]


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _json(text: str):
    def invalid_constant(value):
        raise ValueError(f"Invalid JSON constant: {value}")

    def finite_float(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("Nonfinite JSON number")
        return parsed

    return json.loads(
        text,
        object_pairs_hook=_unique_object,
        parse_constant=invalid_constant,
        parse_float=finite_float,
    )


def _object_end(text: str) -> int | None:
    depth, quoted, escaped = 0, False, False
    for index, char in enumerate(text):
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    return None


def _envelope_text(obj: dict) -> str | None:
    """Return the reply carried by a chat-completion envelope, if obj is one."""
    choices = obj.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    choice = choices[0]
    if not isinstance(choice, dict):
        return None
    message = choice.get("message") or choice.get("delta")
    text = message.get("content") if isinstance(message, dict) else choice.get("text")
    return text if isinstance(text, str) else None


_REASONING_NAMES = ("think", "thinking", "thought")
_REASONING_TAG = re.compile(r"<(/?)(think|thinking|thought)(?=[\s>/]|$)", re.IGNORECASE)


def _may_open_guarded_tag(lowered: str) -> bool:
    """Whether text starting with "<" could still grow into a guarded tag.

    Such text is held back until the tag is complete or ruled out, so "<" is
    released as speech only once the opener cannot become a tool call, a
    special token or a reasoning block.
    """
    if any(prefix.startswith(lowered) for prefix in ("<call:", "<|")):
        return True
    name = lowered[2:] if lowered.startswith("</") else lowered[1:]
    return any(candidate.startswith(name) for candidate in _REASONING_NAMES)


# Underscored names are never words; trailing segments such as ":null" or
# "_0.25" belong to the same token.
_IDENTIFIER = re.compile(
    r"[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]*(?:[.:-][A-Za-z0-9_]+)*[.:]?"
)
_HEADING = re.compile(r"#{1,6}[ \t]")
_RULE = re.compile(r"(?:-{3,}|_{3,}|\*{3,})[ \t]*(?:\n|$)")
_NO_THOUGHT = re.compile(r"(?:no|non)-thought")
_INVISIBLE = "\ufeff\u200b\u2060"


class SpokenToolCallGuard:
    """Suppress tool/thought fragments and collect schema-valid tool candidates."""

    # An unterminated fragment must not grow without limit or escape to speech.
    MAX_PENDING_CHARS = 16384
    _THOUGHT_OBJECT = re.compile(r'^\{\s*"(?:thought|action)"\s*:')

    def __init__(
        self, schemas: dict[str, dict[str, Any]], *, spoken_output: bool = True
    ):
        self._schemas = schemas
        self._spoken_output = spoken_output
        self._validators = {}
        for name, schema in schemas.items():
            schema = {"additionalProperties": False, **schema}
            try:
                Draft202012Validator.check_schema(schema)
                self._validators[name] = Draft202012Validator(
                    schema, registry=Registry()
                )
            except SchemaError:
                logger.warning(
                    "Spoken tool recovery disabled for invalid schema: {}", name
                )
        self._buffer = ""
        self._discard_rest = False
        self._at_boundary = True
        self._after_fragment = False
        # A sentence-initial label or glued token held back until the next
        # fragment shows whether it prefixes a tool call.
        self._held = ""
        self._held_boundary = True
        self._reasoning_close = None
        self._tool_fragment = False
        self._reinject = None
        names = (
            "|".join(re.escape(name) for name in sorted(schemas, key=len, reverse=True))
            or r"(?!)"
        )
        delimiter = r"(?=$|[\s{}!?<>`]|\.(?=\s|$))"
        # Prefer exact advertised names, including punctuation. The generic
        # fallback only suppresses unavailable namespace calls; it cannot run
        # them because _record still requires the advertised schema.
        control_names = rf"{names}|[\w.-]+"
        self._namespace = re.compile(
            rf"(?:[\w-]+:)?default_api[:.]({control_names}){delimiter}"
        )
        self._xml_call = re.compile(rf"<call:default_api:({control_names})\s*/>")
        self._stage_direction = re.compile(
            rf"\*\(\s*Esegue\s+({control_names})(?:\s+per terminare la chiamata)?\s*\)\*",
            re.IGNORECASE,
        )
        self._bare_tool = re.compile(rf"(?:[\w-]+:\s*)?({names}){delimiter}")
        self._bare_call = re.compile(
            rf"(?:[\w-]+:\s*)?({names})\s*(\{{.*\}})?\.?", re.DOTALL
        )
        self.calls: list[RecoveredToolCall] = []

    def feed(self, text: str) -> str:
        if self._discard_rest:
            return ""
        self._buffer += text
        return self._drain(final=False)

    def finish(self) -> str:
        return self._drain(final=True)

    def _record(self, name: str, arguments: Any) -> None:
        self._tool_fragment = True
        name = re.sub(r"^default_api[:.]", "", name)
        validator = self._validators.get(name)
        valid = False
        if validator is not None and isinstance(arguments, dict):
            try:
                valid = validator.is_valid(arguments)
            except (Unresolvable, RecursionError):
                pass
        if valid:
            self.calls.append(RecoveredToolCall(name, arguments))
        else:
            logger.info(
                "Spoken tool fragment suppressed: tool={} recovery=invalid_or_unavailable",
                name,
            )

    @staticmethod
    def _suppressed(kind: str) -> None:
        logger.info("Spoken control text suppressed: kind={}", kind)

    def _speakable(self, token: str) -> str:
        """Drop markdown emphasis and lone table pipes from a spoken token."""
        if not self._spoken_output:
            return token
        token = token.replace("**", "")
        return token.replace("|", "") if token.strip() == "|" else token

    def _arguments(self, name: str, raw: str) -> dict | None:
        try:
            return _json(raw)
        except (ValueError, RecursionError):
            pass
        # Gemini occasionally emits unquoted field names and string values.
        # Only declared keys delimit values; commas inside a value are retained.
        properties = self._schemas.get(name, {}).get("properties", {})
        if not properties or not raw.endswith("}"):
            return None
        body = raw[1:-1]
        keys = "|".join(re.escape(key) for key in properties)
        matches = list(re.finditer(rf"(?:^|,)\s*({keys})\s*:", body))
        if not matches or matches[0].start() != 0:
            return None
        result = {}
        for index, match in enumerate(matches):
            key = match[1]
            if key in result:
                return None
            end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
            value = body[match.end() : end].strip()
            if not value or any(char in value for char in '{}"'):
                return None
            try:
                result[key] = _json(value)
            except ValueError:
                result[key] = value
        return result

    def _keyed_call(self, obj: dict) -> bool:
        """Record {"<tool>": {...}} or {"name": "<tool>", "arguments": ...}."""
        if len(obj) == 1:
            name, args = next(iter(obj.items()))
            if name in self._schemas and isinstance(args, dict):
                self._record(name, args)
                return True
        name = obj.get("name")
        keys = set(obj) - {"name"}
        if isinstance(name, str) and name in self._schemas and len(keys) == 1:
            args = obj[keys.pop()] if keys <= {"arguments", "parameters"} else None
            if isinstance(args, str):
                try:
                    args = _json(args)
                except (ValueError, RecursionError):
                    args = None
            if args is not None:
                self._record(name, args)
                return True
        return False

    def _structured(self, raw: str) -> bool:
        """Recognize a complete wrapper; return whether it should be suppressed."""
        if len(raw) > self.MAX_PENDING_CHARS:
            self._discard_rest = True
            logger.info("Spoken tool fragment suppressed: recovery=buffer_limit")
            return True
        text = raw.strip().lstrip("\ufeff")
        quoted = text.startswith("`") and not text.startswith("```")
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text).removesuffix("```").strip()
        elif quoted:
            # `name`{args} keeps its arguments outside the quotes.
            match = re.fullmatch(r"`+([^`]*)`*\s*(\{.*\})?", text, re.DOTALL)
            text = match[1].strip() + (match[2] or "") if match else text.strip("` ")
        if text.startswith("{"):
            try:
                obj = _json(text)
            except (ValueError, RecursionError):
                if self._spoken_output and re.match(r'\{\s*"', text):
                    self._suppressed("json")
                    return True
                return bool(self._THOUGHT_OBJECT.match(text))
            if not isinstance(obj, dict):
                return False
            if "thought" in obj:
                return True
            if "action" in obj:
                name, args = obj["action"], obj.get("action_input", {})
                if isinstance(args, str):
                    try:
                        args = _json(args)
                    except (ValueError, RecursionError):
                        args = None
                if isinstance(name, str):
                    self._record(name, args)
                return True
            reply = _envelope_text(obj)
            if reply is not None:
                self._suppressed("json_envelope")
                self._reinject = reply
                return True
            if self._keyed_call(obj):
                return True
            if self._spoken_output:
                self._suppressed("json")
                return True
            return False
        xml = self._xml_call.fullmatch(text)
        if xml:
            self._record(xml[1], {})
            return True
        stage_direction = self._stage_direction.fullmatch(text)
        if stage_direction and self._at_boundary:
            self._record(stage_direction[1], {})
            return True
        match = self._namespace.match(text)
        if match:
            tail = text[match.end() :].strip()
            self._record(match[1], self._arguments(match[1], tail) if tail else {})
            return True
        bare = self._bare_call.fullmatch(text)
        if bare and bare[1] in self._schemas:
            if self._at_boundary:
                self._record(
                    bare[1], self._arguments(bare[1], bare[2]) if bare[2] else {}
                )
                return True
            if quoted and self._spoken_output:
                # A code-quoted tool name is never speech, but prose around it
                # is not a request to run it.
                logger.info(
                    "Spoken tool fragment suppressed: tool={} recovery=quoted_mention",
                    bare[1],
                )
                return True
        return False

    def _markup_end(self, text: str, final: bool) -> int | None:
        """Length of sentence-initial markup that is not speech; None to wait."""
        if re.fullmatch(r"[-_*#]{1,2}", text) and not final:
            return None
        rule = _RULE.match(text)
        if rule:
            if rule.end() == len(text) and not text.endswith("\n") and not final:
                return None
            self._suppressed("markdown")
            return rule.end() - text[: rule.end()].endswith("\n")
        if _HEADING.match(text):
            newline = text.find("\n")
            if newline < 0 and not final:
                return None
            self._suppressed("markdown")
            return newline if newline >= 0 else len(text)
        if text.startswith("**"):
            return 0
        if text.startswith("*(") or (
            text.startswith("*") and len(text) > 1 and not text[1].isspace()
        ):
            closer = ")*" if text.startswith("*(") else "*"
            close = text.find(closer, 1)
            end = close + len(closer) if close >= 0 else None
        elif text.startswith(("[", "(")):
            close = text.find("]" if text[0] == "[" else ")")
            end = close + 1 if close >= 0 else None
        else:
            return 0
        newline = text.find("\n")
        if newline >= 0 and (end is None or newline < end):
            end = newline
        if end is None:
            if not final:
                return None
            end = len(text)
        if text[0] == "*":
            # A recognized action still runs; anything else is narration.
            self._structured(text[:end])
        elif not any(char.isalpha() for char in text[:end]):
            return 0
        self._suppressed("stage_direction")
        return end

    def _drain(self, *, final: bool) -> str:
        output = []

        def advance(text):
            for char in text:
                if char in ".!?\n":
                    self._at_boundary = True
                elif not char.isspace():
                    self._at_boundary = False
                if not char.isspace():
                    self._after_fragment = False

        def emit(text):
            if self._held:
                output.append(self._held)
                self._held = ""
            output.append(text)
            advance(text)

        def hold(text):
            self._held_boundary = self._at_boundary
            self._held = text
            advance(text)

        def resolved():
            # A held label or glued token belongs to the tool call after it.
            if self._tool_fragment and self._held:
                self._held = ""
                self._at_boundary = self._held_boundary
            self._tool_fragment = False

        while self._buffer and not self._discard_rest:
            text = self._buffer
            if self._reasoning_close:
                index = text.lower().find(self._reasoning_close)
                close = text.find(">", index) if index >= 0 else -1
                if close < 0:
                    # Keep only what could still complete the closing tag.
                    keep = (
                        index if index >= 0 else len(text) - len(self._reasoning_close)
                    )
                    self._buffer = "" if final else text[max(keep, 0) :]
                    break
                self._buffer = text[close + 1 :]
                self._reasoning_close = None
                continue
            if text[0].isspace():
                if self._held:
                    self._held += text[0]
                else:
                    emit(text[0])
                self._buffer = text[1:]
                continue
            if text[0] in _INVISIBLE or unicodedata.category(text[0]) in ("Mn", "Me"):
                # A zero-width or combining mark cannot start a word; providers
                # surface mis-decoded control tokens this way.
                self._buffer = text[1:]
                continue
            if self._after_fragment and text[0] == "}":
                self._buffer = text[1:]
                continue
            if final and text.strip() == "thought" and self._at_boundary:
                self._buffer = ""
                continue
            if text.startswith("final_thought:") or (
                self._at_boundary and text.startswith("thought:")
            ):
                self._discard_rest = True
                self._buffer = ""
                break
            if text.startswith("italics{thought}"):
                self._buffer = text[len("italics{thought}") :]
                continue
            if "italics{thought}".startswith(text) and not final:
                break
            if self._at_boundary and not final:
                # A period can be part of a declared tool name. Do not emit it
                # as punctuation while the next chunk could complete that name.
                candidate = re.sub(r"^[\w-]+:\s*", "", text)
                if any(name.startswith(candidate) for name in self._schemas):
                    break
            no_thought = _NO_THOUGHT.match(text)
            if (
                no_thought
                and (final or len(text) > no_thought.end())
                and text[no_thought.end() : no_thought.end() + 1]
                in ("", " ", "\n", ".")
            ):
                self._buffer = text[no_thought.end() :]
                continue
            if self._spoken_output and self._at_boundary:
                markup = self._markup_end(text, final)
                if markup is None:
                    break
                if markup:
                    resolved()
                    self._buffer = text[markup:]
                    continue
            end = None
            if text == "*" and not final:
                break
            structured = text.startswith(("{", "`", "<", "*("))
            if text.startswith("{"):
                end = _object_end(text)
            elif text.startswith("`"):
                if text in ("`", "``") and not final:
                    break
                fence = "```" if text.startswith("```") else "`"
                close = text.find(fence, len(fence))
                end = close + len(fence) if close >= 0 else None
                if fence == "`" and end == len(text) and not final:
                    break
                if end is not None and fence == "`" and text[end : end + 1] == "{":
                    # Arguments glued after a quoted name belong to it.
                    close = _object_end(text[end:])
                    if close is None and not final:
                        break
                    end = end + close if close else len(text)
            elif text.startswith("<"):
                if not final and _may_open_guarded_tag(text.lower()):
                    break
                if text.startswith("<|"):
                    close = text.find("|>", 2)
                    if close < 0 and not final and len(text) < 66:
                        break
                    if 2 < close <= 64 and not any(c.isspace() for c in text[2:close]):
                        # Model special tokens such as <|im_end|> are never text.
                        self._suppressed("special_token")
                        self._buffer = text[close + 2 :]
                        continue
                tag = _REASONING_TAG.match(text)
                if tag:
                    close = text.find(">", tag.end())
                    if close < 0 and not final and len(text) < 256:
                        break
                    if close >= 0 or final:
                        self._suppressed("reasoning")
                        if close < 0:
                            self._buffer = ""
                            continue
                        if not tag[1] and text[close - 1] != "/":
                            self._reasoning_close = "</" + tag[2].lower()
                        self._buffer = text[close + 1 :]
                        continue
                if not text.startswith("<call:"):
                    emit("<")
                    self._buffer = text[1:]
                    continue
                close = text.find(">")
                end = close + 1 if close >= 0 else None
            elif text.startswith("*("):
                close = text.find(")*")
                end = close + 2 if close >= 0 else None
            if structured:
                if end is None and not final:
                    break
                end = end or len(text)
                raw = text[:end]
                if self._structured(raw):
                    self._after_fragment = True
                    resolved()
                    if self._reinject is not None:
                        # The reply inside an envelope is ordinary speech.
                        self._buffer = self._reinject + text[end:]
                        self._reinject = None
                        continue
                else:
                    # Incomplete explicit control syntax is suppressed as well.
                    if not re.match(
                        r"(?:`*(?:default_api|\{\s*\"(?:action|thought)\")|<call:)", raw
                    ):
                        emit(raw)
                self._buffer = text[end:]
                continue

            namespace = self._namespace.match(text)
            tool = namespace or self._bare_tool.match(text)
            if tool and (namespace or self._at_boundary):
                suffix = text[tool.end() :]
                stripped = suffix.lstrip()
                if not stripped and not final:
                    break
                if stripped.startswith("{"):
                    close = _object_end(stripped)
                    if close is None and not final:
                        break
                    end = (
                        tool.end()
                        + len(suffix)
                        - len(stripped)
                        + (close or len(stripped))
                    )
                    self._structured(text[:end])
                    self._after_fragment = True
                    resolved()
                    self._buffer = text[end:]
                    continue
                if not stripped or stripped == ".":
                    if not final:
                        break
                    self._structured(text)
                    resolved()
                    self._buffer = ""
                    continue
                # A namespace without an argument object is unambiguous only
                # once a delimiter follows the complete function name.
                if namespace and suffix and (suffix[0].isspace() or suffix[0] in ".!?"):
                    self._structured(text[: tool.end()])
                    resolved()
                    self._buffer = suffix
                    continue

            if self._spoken_output and self._at_boundary:
                identifier = _IDENTIFIER.match(text)
                if identifier:
                    after = text[identifier.end() : identifier.end() + 1]
                    if not after and not final:
                        break
                    if not after or after.isspace() or after in ",;!?)]*`":
                        self._suppressed("identifier")
                        self._buffer = text[identifier.end() :]
                        continue

            # Retain the current lexical token, including a possible junk prefix.
            boundary = re.search(r"[\s{}<>`!?;,.]", text)
            if boundary:
                end = boundary.start() + (boundary[0] in " \t\n\r!?;,.")
                if (
                    text[: boundary.start()].endswith("default_api")
                    and boundary[0] == "."
                ):
                    # The namespace may use a period rather than a colon.
                    break
                if text.startswith("Agent:") and not final:
                    after = text[len("Agent:") :].lstrip()
                    if not after or any(
                        name.startswith(after) for name in self._schemas
                    ):
                        break
                if end == 0:
                    end = 1
                token = text[:end]
                if self._at_boundary and not self._held:
                    # A label ("Prime:") or a word joined to markup ("grid`…`")
                    # opens the sentence; decide once the next fragment is
                    # known. A finished sentence ("Goodbye!") is never held:
                    # it may be the whole reply, and junk is not worth it.
                    glued = (
                        end < len(text)
                        and not token[-1].isspace()
                        and token[-1] not in ".!?"
                        and not text[end].isspace()
                    )
                    if glued or token.rstrip().endswith(":"):
                        hold(self._speakable(token))
                        self._buffer = text[end:]
                        continue
                emit(self._speakable(token))
                self._buffer = text[end:]
            elif final:
                emit(self._speakable(text))
                self._buffer = ""
            else:
                break

        if len(self._buffer) > self.MAX_PENDING_CHARS:
            self._buffer = ""
            self._discard_rest = True
            logger.info("Spoken tool fragment suppressed: recovery=buffer_limit")
        if self._discard_rest:
            self._buffer = ""
        if self._held and (final or self._discard_rest):
            output.append(self._held)
            self._held = ""
        return "".join(output)
