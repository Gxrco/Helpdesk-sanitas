"""Run repository configuration scripts in an initialized Frappe site context.

These modules are ordinary configuration scripts, not installed Frappe apps.
Run with the bench virtualenv's Python from the bench directory.
"""
import argparse
import importlib
import os
from pathlib import Path

TASKS = {
    "configure": ("sanitas_setup", "apply"),
    "demo": ("sanitas_setup", "demo"),
    "validate": ("sanitas_validate", "run"),
}


def run_task(action, site):
    module_name, function_name = TASKS[action]
    import frappe

    bench_directory = Path.cwd()
    connected = False
    try:
        # Like Bench's CLI, Frappe v15 runs from sites/: its logger uses
        # ../logs/database.log and SITE/logs/database.log relative to cwd.
        os.chdir(bench_directory / "sites")
        try:
            frappe.init(site=site)
            frappe.connect()
            connected = True
            frappe.set_user("Administrator")
            # Python import avoids treating repository scripts as installed apps.
            module = importlib.import_module(module_name)
            getattr(module, function_name)()
            if action != "validate":
                frappe.db.commit()
        except BaseException:
            if connected:
                frappe.db.rollback()
            raise
        finally:
            frappe.destroy()
    finally:
        os.chdir(bench_directory)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=TASKS)
    args = parser.parse_args()
    run_task(args.action, os.environ["SITE_NAME"])
