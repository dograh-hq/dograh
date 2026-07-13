from app.session.flow import build_runtime_variables, render_template


class TestBuildRuntimeVariables:
    def test_channel_voice_sip(self):
        config = {"channel": "voice_sip", "sender_phone": "+39123456789", "session_id": "sess_1"}
        vars = build_runtime_variables(config)
        assert vars["channel.name"] == "voice_sip"
        assert vars["channel.is_voice"] is True
        assert vars["channel.is_web_chat"] is False
        assert vars["user.phone"] == "+39123456789"
        assert vars["session.id"] == "sess_1"

    def test_channel_web_chat(self):
        config = {"channel": "web_chat"}
        vars = build_runtime_variables(config)
        assert vars["channel.supports_audio"] is True
        assert vars["channel.is_web_chat"] is True

    def test_memory_variables_merged(self):
        config = {"channel": "voice_sip"}
        memory = {"lead.name": "Mario", "lead.phone": "+39111"}
        vars = build_runtime_variables(config, memory_variables=memory)
        assert vars["lead.name"] == "Mario"
        assert vars["lead.phone"] == "+39111"


class TestRenderTemplate:
    def test_simple_variable(self):
        assert render_template("Hello {{name}}", {"name": "World"}) == "Hello World"

    def test_nested_variable(self):
        values = {"user": {"name": "Mario"}}
        assert render_template("Ciao {{user.name}}", values) == "Ciao Mario"

    def test_missing_variable(self):
        assert render_template("Hello {{missing}}", {}) == "Hello "

    def test_no_template(self):
        assert render_template("Plain text", {}) == "Plain text"
