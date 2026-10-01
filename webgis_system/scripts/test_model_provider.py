"""Offline checks for provider isolation and shared tool registration."""

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
import model_gateway
from agent_tools import TOOL_SCHEMAS, execute_tool


class Response:
    def __init__(self, payload):
        self.body = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def __enter__(self):
        return self.body

    def __exit__(self, *_):
        self.body.close()


class ProviderTests(unittest.TestCase):
    def test_none_never_connects(self):
        with patch.object(config, "MODEL_PROVIDER", "none"), patch.object(
                model_gateway, "urlopen", side_effect=AssertionError("network used")):
            with self.assertRaises(HTTPException) as raised:
                model_gateway.chat([{"role": "user", "content": "test"}])
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail, "当前未启用模型")

    def test_cloud_uses_only_cloud_configuration(self):
        def respond(request, timeout):
            self.assertEqual(request.full_url, "https://example.invalid/v1/chat/completions")
            self.assertEqual(request.get_header("Authorization"), "Bearer dummy")
            payload = json.loads(request.data)
            self.assertEqual(payload["model"], "cloud-test")
            self.assertFalse(payload.get("stream", False))
            return Response({"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

        env = {"MODEL_API_BASE_URL": "https://example.invalid/v1",
               "MODEL_API_KEY": "dummy", "MODEL_NAME": "cloud-test"}
        with patch.object(config, "MODEL_PROVIDER", "cloud"), patch.dict(
                "os.environ", env), patch.object(model_gateway, "urlopen", side_effect=respond):
            self.assertEqual(model_gateway.chat([])["content"], "ok")

    def test_ollama_does_not_read_cloud_configuration(self):
        def respond(request, timeout):
            self.assertEqual(request.full_url, "http://host.docker.internal:11434/api/chat")
            self.assertIsNone(request.get_header("Authorization"))
            payload = json.loads(request.data)
            self.assertEqual(payload["model"], "qwen2.5:7b")
            self.assertIs(payload["stream"], False)
            return Response({"message": {"role": "assistant", "content": "local"}})

        env = {"OLLAMA_BASE_URL": "http://host.docker.internal:11434",
               "OLLAMA_MODEL": "qwen2.5:7b", "MODEL_API_KEY": "must-not-use"}
        with patch.object(config, "MODEL_PROVIDER", "ollama"), patch.dict(
                "os.environ", env), patch.object(model_gateway, "urlopen", side_effect=respond):
            self.assertEqual(model_gateway.chat([])["content"], "local")

    def test_shared_tool_registry(self):
        names = {item["function"]["name"] for item in TOOL_SCHEMAS}
        self.assertTrue({"get_annual_area", "compare_years", "get_point_history",
                         "get_knowledge_graph", "search_documents"}.issubset(names))
        with self.assertRaisesRegex(ValueError, "未登记的工具"):
            execute_tool("arbitrary_command", {})


if __name__ == "__main__":
    unittest.main()
