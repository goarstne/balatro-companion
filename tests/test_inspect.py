"""Local editor HTTP boundaries, replay and isolated revision comparison."""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from balatro.inspect import make_server


class Inspector(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        run = self.base / "example"
        run.mkdir()
        move = {"n": 0, "state": {"phase": "shop", "hand": []}, "msg": "ok", "ok": True}
        (run / "moves.jsonl").write_text(json.dumps(move) + "\n")
        self.server = make_server(self.base)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.prefix = self.server.url.split(f":{self.server.server_port}", 1)[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, route, body=None, headers=None):
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=15)
        try:
            client.request("GET" if body is None else "POST", self.prefix + route,
                           body=json.dumps(body) if body is not None else None,
                           headers={"Content-Type": "application/json", **(headers or {})})
            result = client.getresponse()
            data = result.read()
            return result.status, json.loads(data) if "application/json" in result.getheader("Content-Type") else data
        finally:
            client.close()

    def test_replay_stats_and_selected_score(self):
        self.assertIn(b"Replay", self.request("")[1])
        self.assertEqual(self.request("api/runs")[1]["runs"][0]["run"], "example")
        self.assertEqual(self.request("api/run/example")[1][0]["n"], 0)
        status, result = self.request("api/score", {"state": {"hand": ["AS"]}, "indices": [0]})
        self.assertEqual(status, 200)
        self.assertEqual(result["selected"]["score"], 16)

    def test_rejects_cross_origin_invalid_state_and_run_escape(self):
        body = {"state": {"hand": ["AS"]}, "indices": [0, 0]}
        self.assertEqual(self.request("api/analyze", body)[0], 400)
        self.assertEqual(self.request("api/score", {**body, "indices": [0]}, {"Origin": "https://example.com"})[0], 400)
        self.assertEqual(self.request("api/score", {"state": {"hand": ["AZ"]}, "indices": [0]})[0], 400)
        self.assertEqual(self.request("api/score", {"state": {"hand": ["AS"], "money": float("nan")}, "indices": [0]})[0], 400)
        self.assertEqual(self.request("api/run/../example")[0], 404)
        self.assertEqual(self.request("frame/example/../../secret")[0], 400)
        self.assertEqual(self.request("api/missing")[0], 404)

    def test_revision_comparison_runs_in_an_isolated_copy(self):
        from balatro.benchmark import compare, corpus
        root = Path(__file__).resolve().parent.parent
        snapshot = self.base / "state.json"
        snapshot.write_text(json.dumps({"hand": ["AS"], "hands_left": 1, "discards_left": 0, "blind_chips": 10}))
        report = compare("HEAD", [snapshot], limit=1, samples=2, root=root)
        self.assertEqual(report["states"], 1)
        self.assertEqual(report["changed_decisions"], 0)
        self.assertEqual(report["rng_seed"], 0)
        self.assertIsNotNone(report["before_median_seconds"])
        with self.assertRaises(ValueError):
            corpus([snapshot], samples=0)


if __name__ == "__main__":
    unittest.main()
