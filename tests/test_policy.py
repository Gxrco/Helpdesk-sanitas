"""Offline policy tests: execute the real Server Script with a small Frappe stand-in.

These tests establish business rules, not Frappe compatibility or live permissions.
"""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import unittest

from scripts.backup_manifest import select_files

ROOT = Path(__file__).resolve().parents[1]
POLICY = json.loads((ROOT / "config/policy.json").read_text())
SCRIPT = compile((ROOT / "server_scripts/ticket_policy.py").read_text(), "ticket_policy.py", "exec")
EXPECTED = [
    ("Alto", "Alta", "P1", 900, 14400), ("Alto", "Media", "P1", 1800, 28800),
    ("Alto", "Baja", "P2", 3600, 45000), ("Medio", "Alta", "P2", 3600, 45000),
    ("Medio", "Media", "P3", 14400, 90000), ("Medio", "Baja", "P3", 45000, 135000),
    ("Bajo", "Alta", "P3", 45000, 135000), ("Bajo", "Media", "P4", 90000, 225000),
    ("Bajo", "Baja", "P4", 135000, None),
]


class Ticket(SimpleNamespace):
    def __init__(self, old=None, **kwargs):
        super().__init__(subject="Prueba sintética", description="Sin información real", raised_by="solicitante@sanitas.example",
            custom_requester_role="Recepción", ticket_type="Incidente", custom_module=POLICY["modules"][0],
            custom_impact="Medio", custom_urgency="Media", priority="P4", status="Nuevo", status_category="Open",
            agent_group=None, custom_override_priority=0, custom_override_reason=None, custom_security=0,
            custom_maintenance_date=None, custom_close_evidence=None, custom_demo_id=None,
            custom_related_ticket=None, resolution_details=None)
        self.__dict__.update(kwargs)
        self.previous = old

    def is_new(self):
        return self.previous is None

    def get_doc_before_save(self):
        return self.previous

    def get(self, name):
        return getattr(self, name, None)

    def set_sla(self):
        # Only a marker. SLA selection is tested against real Frappe by sanitas_validate.
        self.sla_reselected = True


def execute(doc, agent=True):
    def throw(message):
        raise ValueError(message)
    def category(doctype, name, field):
        return next(r["category"] for r in POLICY["statuses"] if r["name"] == name)
    frappe = SimpleNamespace(session=SimpleNamespace(user="nivel1@sanitas.example" if agent else "solicitante@sanitas.example"),
        db=SimpleNamespace(exists=lambda *a: agent, get_value=category), throw=throw)
    exec(SCRIPT, {"doc": doc, "frappe": frappe})
    return doc


class PolicyTests(unittest.TestCase):
    def test_native_bench_backup_archive_formats_and_incomplete_sets(self):
        for extension in ["tgz", "tar", "tar.gz"]:
            with self.subTest(extension=extension), TemporaryDirectory() as folder:
                root = Path(folder)
                prefix = "20261025_200000-helpdesk_localhost"
                (root / (prefix + "-database.sql.gz")).write_bytes(b"synthetic")
                with self.assertRaises(RuntimeError):
                    select_files(root)
                for kind in ["files", "private-files"]:
                    (root / f"{prefix}-{kind}.{extension}").write_bytes(b"synthetic")
                files = select_files(root)
                self.assertEqual(files["private"], f"{prefix}-private-files.{extension}")
                (root / "20261026_200000-helpdesk_localhost-partial-database.sql.gz").write_bytes(b"partial")
                self.assertEqual(select_files(root), files)

    def test_all_nine_priorities_and_sla_targets(self):
        for impact, urgency, priority, response, resolution in EXPECTED:
            with self.subTest(impact=impact, urgency=urgency):
                doc = execute(Ticket(custom_impact=impact, custom_urgency=urgency))
                self.assertEqual(doc.priority, priority)
                row = next(r for r in POLICY["sla"] if (r["impact"], r["urgency"]) == (impact, urgency))
                self.assertEqual((row["priority"], row["response"], row["resolution"]), (priority, response, resolution))
                self.assertTrue(doc.sla_reselected)

    def test_same_priority_different_sla(self):
        rows = [r for r in POLICY["sla"] if r["priority"] == "P1"]
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["response"], rows[1]["response"])
        self.assertNotEqual(rows[0]["resolution"], rows[1]["resolution"])

    def test_security_forces_critical_and_hierarchical_escalation(self):
        for agent in [True, False]:
            doc = execute(Ticket(custom_security=1, custom_impact="Bajo", custom_urgency="Baja"), agent)
            self.assertEqual((doc.priority, doc.status, doc.agent_group), ("P1", "Escalado jerárquicamente", "SANITAS Responsable"))

    def test_security_does_not_block_later_resolution(self):
        old = execute(Ticket(custom_security=1))
        doc = copy.copy(old)
        doc.previous = old
        doc.status = "Resuelto"
        doc.resolution_details = "Contención sintética completada"
        execute(doc)
        self.assertEqual(doc.status, "Resuelto")

    def test_manual_override_requires_reason(self):
        with self.assertRaises(ValueError):
            execute(Ticket(custom_override_priority=1, priority="P1"))
        doc = execute(Ticket(custom_override_priority=1, priority="P2", custom_override_reason="Corrección documentada"))
        self.assertEqual(doc.priority, "P2")
        reply = copy.copy(doc)
        reply.previous = doc
        execute(reply, False)
        self.assertEqual(reply.priority, "P2")

    def test_requester_cannot_inject_internal_fields_on_create(self):
        doc = execute(Ticket(raised_by="otra@sanitas.example", priority="P1", status="Resuelto", agent_group="SANITAS Nivel 3",
            resolution_details="Inyectada", custom_override_priority=1, custom_override_reason="Inyectada", custom_demo_id="INJECTED"), False)
        self.assertEqual(doc.raised_by, "solicitante@sanitas.example")
        self.assertEqual(doc.agent_group, "SANITAS Nivel 1")
        self.assertEqual(doc.status, "Nuevo")
        self.assertEqual(doc.priority, "P3")
        self.assertIsNone(doc.resolution_details)
        self.assertIsNone(doc.custom_demo_id)

    def test_requester_cannot_modify_internal_fields(self):
        old = execute(Ticket(), False)
        for field, value in [("priority", "P1"), ("agent_group", "SANITAS Nivel 2"), ("resolution_details", "Cambio")]:
            with self.subTest(field=field):
                doc = copy.copy(old)
                doc.previous = old
                setattr(doc, field, value)
                with self.assertRaises(ValueError):
                    execute(doc, False)

    def test_requester_cannot_update_another_ticket(self):
        old = Ticket(raised_by="solicitante2@sanitas.example")
        with self.assertRaises(ValueError):
            execute(Ticket(old=old), False)

    def test_closure_requires_resolution_and_evidence(self):
        with self.assertRaises(ValueError):
            execute(Ticket(status="Resuelto"))
        with self.assertRaises(ValueError):
            execute(Ticket(status="Closed", resolution_details="Resuelto"))
        execute(Ticket(status="Closed", resolution_details="Resuelto", custom_close_evidence="Validación ficticia"))

    def test_missing_fields_are_rejected(self):
        for field in ["subject", "description", "custom_requester_role", "ticket_type", "custom_module", "custom_impact", "custom_urgency"]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                execute(Ticket(**{field: ""}))

    def test_pause_and_resolution_categories(self):
        self.assertEqual(execute(Ticket(status="Esperando usuario")).status_category, "Paused")
        self.assertEqual(execute(Ticket(status="Resuelto", resolution_details="Solución sintética")).status_category, "Resolved")

    def test_demo_coverage_and_unique_keys(self):
        rows = json.loads((ROOT / "config/demo.json").read_text())
        self.assertGreaterEqual(len(rows), 15)
        self.assertEqual(len(rows), len({r["id"] for r in rows}))
        self.assertEqual({r["type"] for r in rows}, set(POLICY["ticket_types"]))
        self.assertEqual({r["module"] for r in rows}, set(range(8)))
        self.assertTrue({"Nuevo", "Resuelto", "Closed", "Escalado funcionalmente", "Escalado jerárquicamente"}.issubset({r["status"] for r in rows}))
        priorities = {execute(Ticket(custom_impact=r["impact"], custom_urgency=r["urgency"])).priority for r in rows if r["type"] == "Incidente"}
        self.assertEqual(priorities, {"P1", "P2", "P3", "P4"})

    def test_business_day_is_twelve_and_a_half_hours(self):
        self.assertEqual(POLICY["business_day_seconds"], int(12.5 * 3600))
        self.assertIsNone(POLICY["sla"][-1]["resolution"])

    def test_maintenance_does_not_invent_resolution_deadline(self):
        script = (ROOT / "server_scripts/maintenance_target.py").read_text()
        doc = SimpleNamespace(sla="SANITAS Bajo-Baja", custom_security=0, resolution_by="2026-10-26", response_by="2026-10-29")
        def db_set(field, value, update_modified):
            self.assertFalse(update_modified)
            setattr(doc, field, value)
        doc.db_set = db_set
        exec(script, {"doc": doc})
        self.assertIsNone(doc.resolution_by)
        self.assertEqual(doc.response_by, "2026-10-29")


if __name__ == "__main__":
    unittest.main()
