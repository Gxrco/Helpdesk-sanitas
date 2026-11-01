"""Regressions for reapplying configuration to protected native status records."""
from copy import deepcopy
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


class StatusDocument:
    def __init__(self, api, values):
        self.api = api
        self.data = deepcopy(values)
        self.before = deepcopy(values)

    def get(self, field):
        return self.data.get(field)

    def update(self, values):
        self.data.update(values)

    def save(self, **kwargs):
        changes = {key for key, value in self.data.items() if value != self.before.get(key)}
        if self.data["name"] == "Closed":
            if not changes.intersection({"color", "order"}):
                raise RuntimeError("Closed rejects unchanged saves")
            if changes - {"color", "order"}:
                raise RuntimeError("Closed has protected fields")
        self.api.records[self.data["name"]] = deepcopy(self.data)
        self.api.saves.append(self.data["name"])

    def insert(self, **kwargs):
        self.api.records[self.data["name"]] = deepcopy(self.data)


class StatusAPI:
    def __init__(self):
        self.records = {"Closed": dict(name="Closed", label_agent="Closed", category="Resolved",
            enabled=1, order=4, color="Green", different_view=0, label_customer=None)}
        self.saves = []
        self.db = SimpleNamespace(exists=lambda doctype, name: name in self.records)

    def get_meta(self, doctype):
        assert doctype == "HD Ticket Status"
        return SimpleNamespace(fields=[SimpleNamespace(fieldname=f) for f in
            ["label_agent", "category", "enabled", "order", "color", "different_view", "label_customer"]])

    def get_doc(self, doctype, name=None):
        values = doctype if isinstance(doctype, dict) else self.records[name]
        return StatusDocument(self, values)


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.api = StatusAPI()
        modules = {
            "frappe": self.api,
            "frappe.custom.doctype.custom_field.custom_field": SimpleNamespace(create_custom_fields=Mock()),
            "frappe.custom.doctype.property_setter.property_setter": SimpleNamespace(make_property_setter=Mock()),
            "frappe.permissions": SimpleNamespace(add_permission=Mock(), update_permission_property=Mock()),
        }
        spec = importlib.util.spec_from_file_location("sanitas_setup_status_test", ROOT / "scripts/sanitas_setup.py")
        self.setup = importlib.util.module_from_spec(spec)
        with patch.dict("sys.modules", modules):
            spec.loader.exec_module(self.setup)

    def test_status_configuration_can_repeat_without_saving_unchanged_closed(self):
        before = deepcopy(self.api.records["Closed"])
        for _ in range(3):
            self.setup.configure_statuses(self.setup.policy())
        self.assertEqual(len(self.api.records), 10)
        self.assertEqual(self.api.saves.count("Closed"), 1)
        for key in before.keys() - {"order", "color"}:
            self.assertEqual(self.api.records["Closed"][key], before[key])

    def test_existing_customer_label_is_preserved(self):
        self.api.records["Closed"].update(different_view=1, label_customer="Cerrado")
        self.setup.configure_statuses(self.setup.policy())
        self.assertEqual(self.api.records["Closed"]["label_customer"], "Cerrado")
        self.assertEqual(self.api.records["Closed"]["different_view"], 1)

    def test_upsert_skips_unchanged_save_but_saves_real_changes(self):
        self.setup.upsert("HD Ticket Status", "Closed", {"order": 4, "color": "Green"})
        self.assertEqual(self.api.saves, [])
        self.setup.upsert("HD Ticket Status", "Closed", {"order": 10, "color": "Green"})
        self.assertEqual(self.api.saves, ["Closed"])

    def test_missing_native_closed_is_reported_without_recreating_it(self):
        del self.api.records["Closed"]
        with self.assertRaisesRegex(RuntimeError, "Falta el estado nativo Closed"):
            self.setup.configure_statuses(self.setup.policy())
        self.assertNotIn("Closed", self.api.records)


if __name__ == "__main__":
    unittest.main()
