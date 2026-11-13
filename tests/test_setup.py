import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import bootstrap


@unittest.skipUnless(sys.platform == "win32", "The first launcher target is Windows.")
class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "requirements.txt").write_text("pydantic>=2,<3\n", encoding="utf-8")
        (self.root / "requirements-dev.txt").write_text(
            "-r requirements.txt\npytest>=8,<10\n", encoding="utf-8",
        )
        self.root_patch = patch.object(bootstrap, "ROOT", self.root)
        self.root_patch.start()

    def tearDown(self):
        self.root_patch.stop()
        self.temporary.cleanup()

    def test_setup_without_an_approved_source_makes_no_install_attempt(self):
        with patch.object(sys, "argv", ["bootstrap", "--setup"]), patch(
            "tools.bootstrap.subprocess.run"
        ) as run:
            with self.assertRaisesRegex(RuntimeError, "offline by default"):
                bootstrap.main()
            run.assert_not_called()
        self.assertFalse((self.root / ".venv").exists())

    def test_normal_launch_never_installs_dependencies(self):
        with patch.object(sys, "argv", ["bootstrap"]), patch(
            "tools.bootstrap.subprocess.run"
        ) as run:
            with self.assertRaisesRegex(RuntimeError, "approved package source"):
                bootstrap.main()
            run.assert_not_called()

    def test_wheelhouse_installation_is_explicit_and_has_no_package_index(self):
        wheels = self.root / "approved wheels"
        wheels.mkdir()
        with patch.object(
            sys, "argv", ["bootstrap", "--setup", "--wheelhouse", str(wheels), "--yes"]
        ), patch("tools.bootstrap.subprocess.run") as run, patch(
            "tools.bootstrap.subprocess.call", return_value=0
        ):
            def create_environment(command, **_kwargs):
                if command[1:3] == ["-m", "venv"]:
                    executable = self.root / ".venv" / "Scripts" / "python.exe"
                    executable.parent.mkdir(parents=True)
                    executable.write_bytes(b"test placeholder, not a runtime")
            run.side_effect = create_environment
            self.assertEqual(bootstrap.main(), 0)
            installation = next(
                call.args[0] for call in run.call_args_list if "install" in call.args[0]
            )
            self.assertIn("--no-index", installation)
            self.assertNotIn("--index-url", installation)
            self.assertNotIn("--trusted-host", installation)
            self.assertIn(str(wheels.resolve()), installation)


if __name__ == "__main__":
    unittest.main()
