"""Instructor authentication and relay errors, without touching the live lab."""
import pathlib
import sys
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import app as noc


class InstructorAuthTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(noc.app)
        self.key = patch.object(noc, "API_KEY", "test-control-key")
        self.key.start()
        self.addCleanup(self.key.stop)

    def test_wrong_key_never_reaches_control_services(self):
        with patch.object(noc, "_relay", new_callable=AsyncMock) as relay:
            r = self.client.post("/api/inject", params={"fault": "test", "key": "wrong"})
        self.assertEqual(r.status_code, 401)
        relay.assert_not_awaited()

    def test_empty_server_key_does_not_open_controls(self):
        with patch.object(noc, "API_KEY", ""), patch.object(noc, "_relay", new_callable=AsyncMock) as relay:
            r = self.client.post("/api/reset")
        self.assertEqual(r.status_code, 401)
        relay.assert_not_awaited()

    def test_upstream_auth_failure_is_server_configuration_error(self):
        cases = [
            ("/api/inject", {"fault": "test"}, "envsim"),
            ("/api/inj/inject", {"id": "test", "target": "test"}, "injector"),
            ("/api/reset", {}, "envsim"),
            ("/api/inj/clear_all", {}, "injector"),
        ]
        for path, params, service in cases:
            with self.subTest(path=path), patch.object(httpx.AsyncClient, "request", new_callable=AsyncMock) as request:
                request.return_value = httpx.Response(401, json={"detail": "old key rejected"})
                r = self.client.post(path, params={**params, "key": "test-control-key"})
            self.assertEqual(r.status_code, 503)
            self.assertIn(service, r.json()["detail"])
            self.assertIn("서버 간 인증", r.json()["detail"])
            self.assertNotIn("test-control-key", r.text)

    def test_control_success_and_upstream_errors_are_preserved(self):
        for path in ("/api/reset", "/api/inj/clear_all"):
            for status in (200, 409, 500):
                with self.subTest(path=path, status=status), patch.object(httpx.AsyncClient, "request", new_callable=AsyncMock) as request:
                    request.return_value = httpx.Response(status, json={"detail": "result"})
                    r = self.client.post(path, params={"key": "test-control-key"})
                self.assertEqual(r.status_code, status)
                self.assertEqual(r.json(), {"detail": "result"})
                self.assertEqual(request.call_args.kwargs["params"]["key"], "test-control-key")

    def test_connection_exception_does_not_disclose_key_in_url(self):
        with patch.object(httpx.AsyncClient, "request", new_callable=AsyncMock) as request:
            request.side_effect = httpx.ConnectError("http://envsim/reset?key=test-control-key")
            r = self.client.post("/api/reset", params={"key": "test-control-key"})
        self.assertEqual(r.status_code, 502)
        self.assertNotIn("test-control-key", r.text)


if __name__ == "__main__":
    unittest.main()
