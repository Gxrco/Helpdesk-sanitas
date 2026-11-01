"""Exercise the real demo loader with native-hook updates simulated in storage."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


class TimestampMismatchError(RuntimeError):
    pass


class Ticket:
    def __init__(self, api, values):
        object.__setattr__(self, "api", api)
        object.__setattr__(self, "data", deepcopy(values))

    def __getattr__(self, name):
        return self.data.get(name)

    def __setattr__(self, name, value):
        self.data[name] = value

    def insert(self, **kwargs):
        self.name = str(len(self.api.tickets) + 1)
        self.modified = 1
        self.status = "Nuevo"
        self.api.tickets[self.name] = deepcopy(self.data)
        # Like the initial Communication hook: saves the stored ticket,
        # leaving the caller's document at an earlier timestamp.
        self.api.tickets[self.name]["modified"] += 1
        return self

    def reload(self):
        object.__setattr__(self, "data", deepcopy(self.api.tickets[self.name]))
        return self

    def save(self, **kwargs):
        stored = self.api.tickets[self.name]
        if self.api.concurrent_edit:
            stored["modified"] += 1
            self.api.concurrent_edit = False
        if self.modified != stored["modified"]:
            raise TimestampMismatchError("Document has been modified")
        self.modified += 1
        self.api.tickets[self.name] = deepcopy(self.data)
        return self

    def reply_via_agent(self, message, to):
        stored = self.api.tickets[self.name]
        stored["modified"] += 1
        stored.setdefault("replies", []).append(message)

    def assign_agent(self, agent):
        stored = self.api.tickets[self.name]
        stored["modified"] += 1
        stored["assigned_to"] = agent

    def new_comment(self, content):
        stored = self.api.tickets[self.name]
        stored["modified"] += 1
        stored.setdefault("comments", []).append(content)


class FrappeStandIn:
    def __init__(self):
        self.tickets = {}
        self.concurrent_edit = False
        self.db = SimpleNamespace(exists=self.exists, get_value=self.get_value, commit=Mock())

    def set_user(self, user):
        self.user = user

    def exists(self, doctype, filters):
        return self.get_value(doctype, filters, "name")

    def get_value(self, doctype, filters, field):
        assert doctype == "HD Ticket"
        return next((row.get(field) for row in self.tickets.values()
                     if all(row.get(key) == value for key, value in filters.items())), None)

    def get_doc(self, values, name=None):
        if isinstance(values, dict):
            return Ticket(self, values)
        assert values == "HD Ticket"
        return Ticket(self, self.tickets[name])


class DemoTests(unittest.TestCase):
    def setUp(self):
        self.api = FrappeStandIn()
        # Load the actual production module; only Frappe imports are substituted.
        modules = {
            "frappe": self.api,
            "frappe.custom.doctype.custom_field.custom_field": SimpleNamespace(create_custom_fields=Mock()),
            "frappe.custom.doctype.property_setter.property_setter": SimpleNamespace(make_property_setter=Mock()),
            "frappe.permissions": SimpleNamespace(add_permission=Mock(), update_permission_property=Mock()),
        }
        spec = importlib.util.spec_from_file_location("sanitas_setup_demo_test", ROOT / "scripts/sanitas_setup.py")
        self.setup = importlib.util.module_from_spec(spec)
        with patch.dict("sys.modules", modules):
            spec.loader.exec_module(self.setup)
        self.rows = json.loads((ROOT / "config/demo.json").read_text())

    def load(self):
        with patch("builtins.print"):
            self.setup.demo()

    def test_all_demo_tickets_survive_communication_hook_updates(self):
        self.load()
        self.assertEqual(len(self.api.tickets), 18)
        actual = {row["custom_demo_id"]: row for row in self.api.tickets.values()}
        users = self.setup.policy()["users"]
        for expected in self.rows:
            row = actual[expected["id"]]
            self.assertEqual(row["status"], expected["status"])
            self.assertEqual(row["assigned_to"], next(u["email"] for u in users if u.get("team") == expected["team"]))
            self.assertEqual(len(row["comments"]), 1)
        for demo_id in ["SAN-DEMO-005", "SAN-DEMO-012", "SAN-DEMO-013"]:
            self.assertEqual(actual[demo_id]["custom_related_ticket"], actual["SAN-DEMO-014"]["name"])

    def test_rerun_preserves_existing_tickets_and_does_not_duplicate(self):
        self.load()
        self.api.tickets["1"]["status"] = "En solución"
        before = deepcopy(self.api.tickets)
        self.load()
        self.assertEqual(self.api.tickets, before)

    def test_unexpected_concurrent_edits_are_still_rejected(self):
        self.api.concurrent_edit = True
        with self.assertRaises(TimestampMismatchError):
            self.load()
        self.api.db.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
