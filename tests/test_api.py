import importlib.util
import tempfile
import unittest
from pathlib import Path

AVAILABLE = all(importlib.util.find_spec(name) for name in ("fastapi", "httpx"))


@unittest.skipUnless(AVAILABLE, "API dependencies are unavailable; no downloads are attempted.")
class ApiTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        from clipper.api import create_app
        from clipper.config import Config

        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.video = self.root / "video.mp4"
        self.video.write_bytes(b"unit test fixture")
        self.client = TestClient(
            create_app(Config(data_dir=self.root / "data", worker_enabled=False)),
            base_url="http://127.0.0.1:8765",
        )
        self.client.__enter__()
        self.token = self.client.get("/api/bootstrap").json()["token"]
        self.headers = {"X-Clipper-Token": self.token}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temporary.cleanup()

    def create(self):
        response = self.client.post(
            "/api/projects", headers=self.headers,
            json={"name": "Project", "source": str(self.video)},
        )
        self.assertEqual(response.status_code, 201)
        return response.json()

    def test_mutations_require_session_token_and_reject_external_origins(self):
        body = {"name": "Test", "source": str(self.video)}
        self.assertEqual(self.client.post("/api/projects", json=body).status_code, 403)
        self.assertEqual(
            self.client.post(
                "/api/projects", json=body,
                headers={**self.headers, "Origin": "https://example.com"},
            ).status_code, 403,
        )
        self.assertEqual(
            self.client.get("/api/bootstrap", headers={"Host": "evil.example"}).status_code, 400,
        )

    def test_job_lifecycle_and_revision_conflicts(self):
        project = self.create()
        path = f"/api/projects/{project['id']}"
        edit = {"revision": 0, "settings": project["settings"], "clips": [], "cues": []}
        self.assertEqual(self.client.put(path, headers=self.headers, json=edit).status_code, 200)
        self.assertEqual(self.client.put(path, headers=self.headers, json=edit).status_code, 409)
        job = self.client.post(
            f"{path}/jobs", headers=self.headers, json={"action": "inspect"},
        )
        self.assertEqual(job.status_code, 202)
        duplicate = self.client.post(
            f"{path}/jobs", headers=self.headers, json={"action": "inspect"},
        )
        self.assertEqual(duplicate.status_code, 409)
        cancelled = self.client.post(
            f"/api/jobs/{job.json()['id']}/cancel", headers=self.headers, json={},
        )
        self.assertEqual(cancelled.json()["status"], "cancelled")

    def test_only_registered_outputs_are_served(self):
        project = self.create()
        response = self.client.get(f"/api/projects/{project['id']}/artifacts/logs/secret.txt")
        self.assertEqual(response.status_code, 404)
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])

    def test_delete_requires_token_and_preserves_files(self):
        project = self.create()
        path = f"/api/projects/{project['id']}"
        exported = self.root / "data" / "projects" / project["id"] / "exports" / "clip.mp4"
        exported.parent.mkdir(parents=True)
        exported.write_bytes(b"keep exported video")
        response = self.client.request("DELETE", f"{path}?revision=0", json={})
        self.assertEqual(response.status_code, 403)
        response = self.client.request(
            "DELETE", f"{path}?revision=0", headers=self.headers, json={},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["files_preserved"])
        self.assertEqual(self.client.get("/api/projects").json(), [])
        self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.get(f"{path}/source").status_code, 404)
        self.assertEqual(
            self.client.post(
                f"{path}/jobs", headers=self.headers, json={"action": "inspect"},
            ).status_code, 404,
        )
        self.assertEqual(exported.read_bytes(), b"keep exported video")
        self.assertEqual(self.video.read_bytes(), b"unit test fixture")

    def test_delete_rejects_stale_revision_and_active_job(self):
        project = self.create()
        path = f"/api/projects/{project['id']}"
        edit = {"revision": 0, "settings": project["settings"], "clips": [], "cues": []}
        self.assertEqual(self.client.put(path, headers=self.headers, json=edit).status_code, 200)
        self.assertEqual(
            self.client.request(
                "DELETE", f"{path}?revision=0", headers=self.headers, json={},
            ).status_code, 409,
        )
        job = self.client.post(
            f"{path}/jobs", headers=self.headers, json={"action": "inspect"},
        ).json()
        self.assertEqual(
            self.client.request(
                "DELETE", f"{path}?revision=1", headers=self.headers, json={},
            ).status_code, 409,
        )
        self.client.post(f"/api/jobs/{job['id']}/cancel", headers=self.headers, json={})
        self.assertEqual(
            self.client.request(
                "DELETE", f"{path}?revision=1", headers=self.headers, json={},
            ).status_code, 200,
        )


if __name__ == "__main__":
    unittest.main()
