import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.openai_subscription_demo import (
    DEMO_TOOL_UUID,
    LookupHandler,
    evidence_template,
    lookup_response,
    main,
    validate_fixture,
    workflow_definition,
)


class SubscriptionDemoTests(unittest.TestCase):
    def test_fixture_passes_real_dograh_schemas_and_graph_constraints(self):
        validate_fixture()
        graph = workflow_definition(DEMO_TOOL_UUID)["workflow_definition"]
        self.assertEqual(
            [n["type"] for n in graph["nodes"]], ["startCall", "agentNode", "endCall"]
        )
        self.assertEqual(len(graph["edges"]), 2)

    def test_lookup_returns_distinct_synthetic_values(self):
        for item, location in (("amber", "Bay 4"), ("cyan", "Bay 9")):
            with self.subTest(item=item):
                self.assertEqual(
                    lookup_response(f"/subscription-demo/lookup?item={item}"),
                    (200, {"synthetic": True, "item": item, "location": location}),
                )

    def test_unknown_path_is_rejected(self):
        self.assertEqual(lookup_response("/private?item=amber")[0], 404)

    def test_malformed_or_arbitrary_inputs_are_not_echoed(self):
        for query in (
            "",
            "item=",
            "item=secret-value",
            "item=amber&item=cyan",
            "item=amber&token=secret-value",
        ):
            with self.subTest(query=query):
                status, result = lookup_response(f"/subscription-demo/lookup?{query}")
                self.assertEqual(status, 400)
                self.assertNotIn("secret-value", json.dumps(result))

    def test_http_handler_delays_success_and_logs_only_allowlisted_receipt(self):
        class InMemorySocket:
            def __init__(self, request):
                self.request = io.BytesIO(request)
                self.response = bytearray()

            def makefile(self, *args):
                return self.request

            def sendall(self, value):
                self.response.extend(value)

        request = InMemorySocket(
            b"GET /subscription-demo/lookup?item=cyan HTTP/1.0\r\n"
            b"Authorization: Bearer secret-test-token\r\n\r\n"
        )
        log = io.StringIO()
        with (
            patch("sys.stdout", log),
            patch("scripts.openai_subscription_demo.time.sleep") as sleep,
        ):
            LookupHandler(request, ("127.0.0.1", 0), None)
        sleep.assert_called_once_with(2)
        self.assertIn(b"200 OK", request.response)
        self.assertIn(b'"location": "Bay 9"', request.response)
        self.assertNotIn("secret-test-token", log.getvalue())
        receipt = json.loads(log.getvalue())
        self.assertEqual(
            set(receipt),
            {"time_utc", "event", "status", "item", "location", "elapsed_ms"},
        )

    def test_placeholder_is_not_a_live_receipt(self):
        evidence = evidence_template()
        self.assertEqual(evidence["result"], "NOT_RUN")
        self.assertEqual(evidence["run_ids"], [])
        self.assertEqual(evidence["planned_voice_auth"], "subscription")
        self.assertEqual(evidence["planned_reasoning_auth"], "subscription")
        self.assertEqual(evidence["reasoning_model"], "gpt-5.6-luna")
        self.assertFalse(evidence["developer_api_key_required"])
        self.assertIsNone(evidence["observed_voice_auth"])
        self.assertIsNone(evidence["observed_reasoning_auth"])
        self.assertTrue(
            all(check["result"] == "NOT_RUN" for check in evidence["checks"].values())
        )

    def test_prepare_writes_only_new_pending_local_artifacts(self):
        with TemporaryDirectory() as temporary:
            output = Path(temporary) / "proof"
            argv = [
                "demo",
                "prepare",
                "--tool-uuid",
                DEMO_TOOL_UUID,
                "--output",
                str(output),
            ]
            with patch("sys.argv", argv):
                main()
            self.assertEqual(
                {p.name for p in output.iterdir()},
                {"workflow.json", "tool.json", "evidence.json"},
            )
            before = {p.name: p.read_bytes() for p in output.iterdir()}
            with patch("sys.argv", argv), self.assertRaises(FileExistsError):
                main()
            self.assertEqual(before, {p.name: p.read_bytes() for p in output.iterdir()})
            self.assertEqual(
                json.loads((output / "evidence.json").read_text())["result"], "NOT_RUN"
            )

    def test_invalid_tool_uuid_cannot_generate_an_import(self):
        with self.assertRaises(ValueError):
            workflow_definition("not-a-tool-uuid")


if __name__ == "__main__":
    unittest.main()
