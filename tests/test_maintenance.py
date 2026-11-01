"""Regressions for native validation -> SLA calculation -> save -> After Save order."""
from copy import deepcopy
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


class PersistedTicket(SimpleNamespace):
    def db_set(self, field, value, update_modified=True):
        if update_modified:
            raise AssertionError("Normalization must preserve modified")
        setattr(self, field, value)
        self.stored[field] = value

    def save(self):
        raise AssertionError("After Save must not recursively save the document")


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.api = SimpleNamespace(db=SimpleNamespace(set_value=Mock()))
        modules = {
            "frappe": self.api,
            "frappe.custom.doctype.custom_field.custom_field": SimpleNamespace(create_custom_fields=Mock()),
            "frappe.custom.doctype.property_setter.property_setter": SimpleNamespace(make_property_setter=Mock()),
            "frappe.permissions": SimpleNamespace(add_permission=Mock(), update_permission_property=Mock()),
        }
        spec = importlib.util.spec_from_file_location("sanitas_setup_maintenance_test", ROOT / "scripts/sanitas_setup.py")
        self.setup = importlib.util.module_from_spec(spec)
        with patch.dict("sys.modules", modules):
            spec.loader.exec_module(self.setup)
        self.setup.upsert = Mock()
        self.setup.configure_maintenance_targets()
        self.script = self.setup.upsert.call_args.args[2]

    def simulate_save(self, doc):
        # Frappe Before Save Server Scripts run in validate. The native controller
        # subsequently computes resolution_by, then db_update, then on_update.
        event = self.script["doctype_event"]
        if event == "Before Save":
            exec(self.script["script"], {"doc": doc})
        doc.resolution_by = "2026-10-26 08:00:00"
        doc.stored = {key: value for key, value in vars(doc).items() if key != "stored"}
        if event == "After Save":
            exec(self.script["script"], {"doc": doc})

    def test_maintenance_date_is_cleared_after_native_calculation_and_persistence(self):
        doc = PersistedTicket(sla="SANITAS Bajo-Baja", custom_security=0, resolution_by=None,
            response_by="2026-10-28 20:30:00", modified="original", status="Nuevo")
        self.simulate_save(doc)
        self.assertEqual(self.script["doctype_event"], "After Save")
        self.assertIsNone(doc.resolution_by)
        self.assertIsNone(doc.stored["resolution_by"])
        self.assertEqual(doc.stored["response_by"], "2026-10-28 20:30:00")
        self.assertEqual(doc.stored["modified"], "original")
        self.simulate_save(doc)
        self.assertIsNone(doc.stored["resolution_by"])

    def test_other_policies_and_security_keep_their_native_resolution_targets(self):
        for sla, security in [("SANITAS Alto-Alta", 0), ("SANITAS Seguridad", 1), ("SANITAS Bajo-Baja", 1)]:
            with self.subTest(sla=sla, security=security):
                doc = PersistedTicket(sla=sla, custom_security=security, resolution_by=None, modified="original")
                self.simulate_save(doc)
                self.assertEqual(doc.stored["resolution_by"], "2026-10-26 08:00:00")

    def test_configuration_repairs_only_existing_maintenance_tickets(self):
        rows = [
            dict(sla="SANITAS Bajo-Baja", custom_security=0, resolution_by="old", response_by="response", modified="original"),
            dict(sla="SANITAS Seguridad", custom_security=1, resolution_by="security", modified="original"),
            dict(sla="SANITAS Bajo-Baja", custom_security=1, resolution_by="security", modified="original"),
        ]
        before = deepcopy(rows)
        args, kwargs = self.api.db.set_value.call_args
        doctype, filters, field, value = args
        self.assertEqual(doctype, "HD Ticket")
        self.assertEqual(kwargs, {"update_modified": False})
        for row in rows:
            if all(row.get(key) == expected for key, expected in filters.items()):
                row[field] = value
        self.assertIsNone(rows[0]["resolution_by"])
        self.assertEqual(rows[0]["response_by"], before[0]["response_by"])
        self.assertEqual(rows[0]["modified"], before[0]["modified"])
        self.assertEqual(rows[1:], before[1:])


if __name__ == "__main__":
    unittest.main()
