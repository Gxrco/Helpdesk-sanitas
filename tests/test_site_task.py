"""Regressions for executing configuration modules without installing a new app."""
import importlib.util
import logging
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts.site_task import run_task


class SiteTaskTests(unittest.TestCase):
    def setUp(self):
        original_directory = Path.cwd()
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.addCleanup(os.chdir, original_directory)
        self.bench = Path(temporary.name).resolve()
        self.sites = self.bench / "sites"
        (self.sites / "helpdesk.localhost" / "logs").mkdir(parents=True)
        (self.bench / "logs").mkdir()
        os.chdir(self.bench)
        self.frappe = self.make_frappe()

    def make_frappe(self):
        def check_directory(*args, **kwargs):
            self.assertEqual(Path.cwd(), self.sites)
        return SimpleNamespace(init=Mock(side_effect=check_directory), connect=Mock(), set_user=Mock(), destroy=Mock(side_effect=check_directory),
            db=SimpleNamespace(commit=Mock(), rollback=Mock()),
            get_attr=Mock(side_effect=AssertionError("Repository scripts are not installed apps")))

    def test_actions_run_with_site_context_without_app_lookup(self):
        for action, module_name, function_name in [
            ("configure", "sanitas_setup", "apply"),
            ("demo", "sanitas_setup", "demo"),
            ("validate", "sanitas_validate", "run"),
        ]:
            with self.subTest(action=action):
                self.frappe = self.make_frappe()
                operation = Mock()
                module = SimpleNamespace(**{function_name: operation})
                with patch.dict("sys.modules", {"frappe": self.frappe, module_name: module}):
                    run_task(action, "helpdesk.localhost")
                self.frappe.init.assert_called_once_with(site="helpdesk.localhost")
                self.assertEqual(Path.cwd(), self.bench)
                self.frappe.connect.assert_called_once()
                self.frappe.set_user.assert_called_once_with("Administrator")
                operation.assert_called_once_with()
                self.frappe.get_attr.assert_not_called()
                self.frappe.destroy.assert_called_once()
                if action == "validate":
                    self.frappe.db.commit.assert_not_called()
                else:
                    self.frappe.db.commit.assert_called_once()

    def test_failed_configuration_rolls_back_and_closes_site(self):
        module = SimpleNamespace(apply=Mock(side_effect=RuntimeError("configuration failed")))
        with patch.dict("sys.modules", {"frappe": self.frappe, "sanitas_setup": module}):
            with self.assertRaisesRegex(RuntimeError, "configuration failed"):
                run_task("configure", "helpdesk.localhost")
        self.frappe.db.rollback.assert_called_once()
        self.frappe.db.commit.assert_not_called()
        self.frappe.destroy.assert_called_once()
        self.assertEqual(Path.cwd(), self.bench)

    def test_connection_failure_still_cleans_up(self):
        self.frappe.connect.side_effect = RuntimeError("database unreachable")
        with patch.dict("sys.modules", {"frappe": self.frappe}):
            with self.assertRaisesRegex(RuntimeError, "database unreachable"):
                run_task("configure", "helpdesk.localhost")
        self.frappe.db.rollback.assert_not_called()
        self.frappe.destroy.assert_called_once()
        self.assertEqual(Path.cwd(), self.bench)

    def test_native_relative_log_paths_use_bench_and_site_logs(self):
        # These are the two real relative paths used by frappe.utils.logger.
        # Opening them from the bench root reproduces the reported exception.
        def connect_with_file_logging():
            for path in [Path("../logs/database.log"), Path("helpdesk.localhost/logs/database.log")]:
                handler = logging.FileHandler(path)
                handler.close()
        self.frappe.connect.side_effect = connect_with_file_logging
        with patch.dict("sys.modules", {"frappe": self.frappe, "sanitas_setup": SimpleNamespace(apply=Mock())}):
            run_task("configure", "helpdesk.localhost")
        self.assertTrue((self.bench / "logs/database.log").is_file())
        self.assertTrue((self.sites / "helpdesk.localhost/logs/database.log").is_file())
        self.assertFalse((self.bench.parent / "logs/database.log").exists())
        self.assertEqual(Path.cwd(), self.bench)

    def test_toolbox_uses_bench_python_without_resolving_venv_symlink(self):
        path = Path(__file__).resolve().parents[1] / "scripts/bench_task.py"
        spec = importlib.util.spec_from_file_location("bench_task_test", path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict("os.environ", {"SITE_NAME": "helpdesk.localhost"}):
            spec.loader.exec_module(module)
        with patch.object(module.Path, "is_file", return_value=True), patch.object(module.subprocess, "run") as run:
            module.site_task("configure")
        run.assert_called_once_with([
            str(Path.cwd() / "env/bin/python"), str(path.with_name("site_task.py")), "configure"
        ], check=True)


if __name__ == "__main__":
    unittest.main()
