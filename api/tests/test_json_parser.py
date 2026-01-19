from api.services.gen_ai.json_parser import (
    _extract_json_array,
    _extract_json_object,
    _try_parse_json,
    parse_llm_json,
)


class TestParseLlmJson:
    """Tests for the main parse_llm_json function."""

    def test_empty_string(self):
        """Empty string returns empty dict."""
        assert parse_llm_json("") == {}

    def test_whitespace_only(self):
        """Whitespace-only string returns empty dict."""
        assert parse_llm_json("   \n\t  ") == {}

    def test_none_handling(self):
        """None input returns empty dict."""
        assert parse_llm_json(None) == {}

    def test_valid_json_direct(self):
        """Valid JSON is parsed directly."""
        result = parse_llm_json('{"name": "John", "age": 30}')
        assert result == {"name": "John", "age": 30}

    def test_valid_json_with_whitespace(self):
        """Valid JSON with surrounding whitespace is parsed."""
        result = parse_llm_json('  \n{"key": "value"}\n  ')
        assert result == {"key": "value"}

    def test_markdown_json_code_block(self):
        """JSON wrapped in ```json ... ``` is extracted and parsed."""
        input_str = """```json
{
  "occupation_of_the_user": "software engineer"
}
```"""
        result = parse_llm_json(input_str)
        assert result == {"occupation_of_the_user": "software engineer"}

    def test_markdown_generic_code_block(self):
        """JSON wrapped in ``` ... ``` (no language) is extracted and parsed."""
        input_str = """```
{"status": "success", "count": 42}
```"""
        result = parse_llm_json(input_str)
        assert result == {"status": "success", "count": 42}

    def test_markdown_with_surrounding_text(self):
        """Markdown code block with text before/after is handled."""
        input_str = """Here is the extracted data:
```json
{"name": "Alice"}
```
I hope this helps!"""
        result = parse_llm_json(input_str)
        assert result == {"name": "Alice"}

    def test_json_with_text_before(self):
        """JSON with explanatory text before is extracted."""
        input_str = 'The result is: {"answer": 42}'
        result = parse_llm_json(input_str)
        assert result == {"answer": 42}

    def test_json_with_text_after(self):
        """JSON with text after is extracted."""
        input_str = '{"found": true} - extraction complete'
        result = parse_llm_json(input_str)
        assert result == {"found": True}

    def test_json_with_text_before_and_after(self):
        """JSON with text on both sides is extracted."""
        input_str = 'Based on the conversation: {"mood": "happy"} is my assessment.'
        result = parse_llm_json(input_str)
        assert result == {"mood": "happy"}

    def test_nested_json_object(self):
        """Nested JSON objects are parsed correctly."""
        input_str = '{"user": {"name": "Bob", "address": {"city": "NYC"}}}'
        result = parse_llm_json(input_str)
        assert result == {"user": {"name": "Bob", "address": {"city": "NYC"}}}

    def test_json_with_string_containing_braces(self):
        """JSON with braces inside strings is parsed correctly."""
        input_str = '{"code": "function() { return {}; }"}'
        result = parse_llm_json(input_str)
        assert result == {"code": "function() { return {}; }"}

    def test_json_with_escaped_quotes(self):
        """JSON with escaped quotes is parsed correctly."""
        input_str = '{"message": "He said \\"hello\\""}'
        result = parse_llm_json(input_str)
        assert result == {"message": 'He said "hello"'}

    def test_json_array_direct(self):
        """JSON array is parsed directly."""
        result = parse_llm_json("[1, 2, 3]")
        assert result == [1, 2, 3]

    def test_json_array_with_objects(self):
        """JSON array of objects is parsed correctly."""
        input_str = '[{"id": 1}, {"id": 2}]'
        result = parse_llm_json(input_str)
        assert result == [{"id": 1}, {"id": 2}]

    def test_json_array_in_markdown(self):
        """JSON array in markdown code block is extracted."""
        input_str = """```json
["apple", "banana", "cherry"]
```"""
        result = parse_llm_json(input_str)
        assert result == ["apple", "banana", "cherry"]

    def test_invalid_json_returns_raw(self):
        """Invalid JSON returns raw content in 'raw' key."""
        input_str = "This is not JSON at all"
        result = parse_llm_json(input_str)
        assert result == {"raw": "This is not JSON at all"}

    def test_malformed_json_returns_raw(self):
        """Malformed JSON returns raw content."""
        input_str = '{"key": "value"'  # Missing closing brace
        result = parse_llm_json(input_str)
        assert result == {"raw": '{"key": "value"'}

    def test_complex_real_world_example(self):
        """Test with a realistic LLM output example."""
        input_str = """Based on our conversation, I've extracted the following information:

```json
{
  "user_name": "John Smith",
  "email": "john@example.com",
  "preferences": {
    "notifications": true,
    "theme": "dark"
  }
}
```

Let me know if you need anything else!"""
        result = parse_llm_json(input_str)
        assert result == {
            "user_name": "John Smith",
            "email": "john@example.com",
            "preferences": {"notifications": True, "theme": "dark"},
        }

    def test_json_with_newlines_inside(self):
        """JSON with newlines inside values is handled."""
        input_str = '{"text": "line1\\nline2"}'
        result = parse_llm_json(input_str)
        assert result == {"text": "line1\nline2"}

    def test_json_with_unicode(self):
        """JSON with unicode characters is parsed correctly."""
        input_str = '{"greeting": "こんにちは", "emoji": "🎉"}'
        result = parse_llm_json(input_str)
        assert result == {"greeting": "こんにちは", "emoji": "🎉"}

    def test_multiple_code_blocks_uses_first(self):
        """When multiple code blocks exist, the first is used."""
        input_str = """```json
{"first": true}
```
Some text
```json
{"second": true}
```"""
        result = parse_llm_json(input_str)
        assert result == {"first": True}


class TestTryParseJson:
    """Tests for the _try_parse_json helper."""

    def test_valid_dict(self):
        assert _try_parse_json('{"a": 1}') == {"a": 1}

    def test_valid_list(self):
        assert _try_parse_json("[1, 2]") == [1, 2]

    def test_invalid_returns_none(self):
        assert _try_parse_json("not json") is None

    def test_primitive_returns_none(self):
        """Primitive values (not dict/list) return None."""
        assert _try_parse_json('"just a string"') is None
        assert _try_parse_json("42") is None
        assert _try_parse_json("true") is None


class TestExtractJsonObject:
    """Tests for the _extract_json_object helper."""

    def test_extracts_from_text(self):
        result = _extract_json_object('prefix {"key": "value"} suffix')
        assert result == {"key": "value"}

    def test_no_object_returns_none(self):
        assert _extract_json_object("no json here") is None

    def test_nested_braces(self):
        result = _extract_json_object('{"outer": {"inner": 1}}')
        assert result == {"outer": {"inner": 1}}

    def test_braces_in_strings(self):
        result = _extract_json_object('{"code": "{ }"}')
        assert result == {"code": "{ }"}


class TestExtractJsonArray:
    """Tests for the _extract_json_array helper."""

    def test_extracts_from_text(self):
        result = _extract_json_array("here is the list: [1, 2, 3] done")
        assert result == [1, 2, 3]

    def test_no_array_returns_none(self):
        assert _extract_json_array("no array here") is None

    def test_nested_arrays(self):
        result = _extract_json_array("[[1, 2], [3, 4]]")
        assert result == [[1, 2], [3, 4]]

    def test_brackets_in_strings(self):
        result = _extract_json_array('["a[b]c"]')
        assert result == ["a[b]c"]
