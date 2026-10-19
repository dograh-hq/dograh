"""Separate accidental tool serialization from streamed conversational text.

The pending buffer holds a lexical token or a structured fragment, never a second
sentence aggregation. Calls are candidates only; the response owner decides when
they may execute, after reconciling native calls and the completion outcome.
"""

import json
import math
import re
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


class SpokenToolCallGuard:
    """Suppress tool/thought fragments and collect schema-valid tool candidates."""

    # An unterminated fragment must not grow without limit or escape to speech.
    MAX_PENDING_CHARS = 16384
    _THOUGHT_OBJECT = re.compile(r'^\{\s*"(?:thought|action)"\s*:')

    def __init__(self, schemas: dict[str, dict[str, Any]]):
        self._schemas = schemas
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
            rf"(?:[\w]+:)?default_api[:.]({control_names}){delimiter}"
        )
        self._xml_call = re.compile(rf"<call:default_api:({control_names})\s*/>")
        self._stage_direction = re.compile(
            rf"\*\(\s*Esegue\s+({control_names})(?:\s+per terminare la chiamata)?\s*\)\*",
            re.IGNORECASE,
        )
        self._bare_tool = re.compile(rf"(?:[\w]+:\s*)?({names}){delimiter}")
        self._bare_call = re.compile(
            rf"(?:[\w]+:\s*)?({names})\s*(\{{.*\}})?\.?", re.DOTALL
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

    def _structured(self, raw: str) -> bool:
        """Recognize a complete wrapper; return whether it should be suppressed."""
        if len(raw) > self.MAX_PENDING_CHARS:
            self._discard_rest = True
            logger.info("Spoken tool fragment suppressed: recovery=buffer_limit")
            return True
        text = raw.strip().lstrip("\ufeff")
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text).removesuffix("```").strip()
        elif text.startswith("`"):
            text = text.strip("` ")
        if text.startswith("{"):
            try:
                obj = _json(text)
            except (ValueError, RecursionError):
                return bool(self._THOUGHT_OBJECT.match(text))
            if not isinstance(obj, dict):
                return False
            if "thought" in obj:
                return True
            if "action" not in obj:
                return False
            name, args = obj["action"], obj.get("action_input", {})
            if isinstance(args, str):
                try:
                    args = _json(args)
                except (ValueError, RecursionError):
                    args = None
            if isinstance(name, str):
                self._record(name, args)
            return True
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
        if bare and bare[1] in self._schemas and self._at_boundary:
            self._record(bare[1], self._arguments(bare[1], bare[2]) if bare[2] else {})
            return True
        return False

    def _drain(self, *, final: bool) -> str:
        output = []

        def emit(text):
            output.append(text)
            for char in text:
                if char in ".!?\n":
                    self._at_boundary = True
                elif not char.isspace():
                    self._at_boundary = False
                if not char.isspace():
                    self._after_fragment = False

        while self._buffer and not self._discard_rest:
            text = self._buffer
            if text[0].isspace() or text[0] == "\ufeff":
                if text[0] != "\ufeff":
                    emit(text[0])
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
                candidate = re.sub(r"^[\w]+:\s*", "", text)
                if any(name.startswith(candidate) for name in self._schemas):
                    break
            if (
                text.startswith("no-thought")
                and (final or len(text) > len("no-thought"))
                and text[len("no-thought") : len("no-thought") + 1]
                in ("", " ", "\n", ".")
            ):
                self._buffer = text[len("no-thought") :]
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
            elif text.startswith("<"):
                if "<call:".startswith(text) and not final:
                    break
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
                    self._buffer = text[end:]
                    continue
                if not stripped or stripped == ".":
                    if not final:
                        break
                    self._structured(text)
                    self._buffer = ""
                    continue
                # A namespace without an argument object is unambiguous only
                # once a delimiter follows the complete function name.
                if namespace and suffix and (suffix[0].isspace() or suffix[0] in ".!?"):
                    self._structured(text[: tool.end()])
                    self._buffer = suffix
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
                emit(text[:end])
                self._buffer = text[end:]
            elif final:
                emit(text)
                self._buffer = ""
            else:
                break

        if len(self._buffer) > self.MAX_PENDING_CHARS:
            self._buffer = ""
            self._discard_rest = True
            logger.info("Spoken tool fragment suppressed: recovery=buffer_limit")
        if self._discard_rest:
            self._buffer = ""
        return "".join(output)
