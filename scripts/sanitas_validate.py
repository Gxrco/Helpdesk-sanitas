"""Real database/permission/SLA validation with rollback. Run through site_task.py."""
import json
from pathlib import Path

import frappe

from sanitas_setup import policy


def check(condition, description):
    if not condition:
        raise AssertionError(description)
    print("PASS " + description)


def expect_denied(operation, description):
    try:
        operation()
    except (frappe.PermissionError, frappe.ValidationError):
        print("PASS " + description)
    else:
        raise AssertionError(description)


def ticket(row, user="solicitante@sanitas.example"):
    frappe.set_user(user)
    p = policy()
    doc = frappe.get_doc(dict(doctype="HD Ticket", subject="VALIDACIÓN TRANSACCIONAL SANITAS",
        description="Solo datos sintéticos; rollback al finalizar.", via_customer_portal=1,
        ticket_type="Incidente", custom_module=p["modules"][0], custom_requester_role="Recepción",
        custom_impact=row["impact"], custom_urgency=row["urgency"]))
    doc.insert()  # Actual permissions and installed Server Script; no ignore_permissions.
    doc.reload()  # Initial Communication hooks can save another ticket instance.
    return doc


def run():
    p = policy()
    original_user = frappe.session.user
    frappe.set_user("Administrator")
    check(frappe.get_single("System Settings").time_zone == p["timezone"], "zona horaria Guatemala")
    for user in p["users"]:
        check(bool(frappe.db.exists("User", user["email"])), "usuario " + user["email"])
    for label in ["Tipo", "Módulo", "Prioridad", "Estado", "SLA"]:
        check(bool(frappe.db.exists("Dashboard Chart", "SANITAS - " + label)), "gráfica configurada: " + label)
    demo_ids = [r["id"] for r in json.loads((Path(__file__).resolve().parents[1] / "config/demo.json").read_text())]
    for demo_id in demo_ids:
        check(frappe.db.count("HD Ticket", {"custom_demo_id": demo_id}) == 1, "demo única " + demo_id)
    frappe.db.savepoint("sanitas_validation")
    try:
        docs = []
        for row in p["sla"]:
            doc = ticket(row)
            docs.append(doc)
            check(doc.priority == row["priority"], f'matriz {row["impact"]}/{row["urgency"]}')
            check(doc.sla == f'SANITAS {row["impact"]}-{row["urgency"]}', "SLA condicional " + doc.sla)
            sla = frappe.get_doc("HD Service Level Agreement", doc.sla)
            target = next(r for r in sla.priorities if r.priority == doc.priority)
            check(target.response_time == row["response"], "primera respuesta " + doc.sla)
            check(bool(sla.apply_sla_for_resolution) == (row["resolution"] is not None), "política de resolución " + doc.sla)
            if row["resolution"] is not None:
                check(target.resolution_time == row["resolution"] and bool(doc.resolution_by), "resolución nativa " + doc.sla)
            else:
                check(not doc.resolution_by, "mantenimiento sin vencimiento fijo")
        check(docs[0].sla != docs[1].sla and docs[0].priority == docs[1].priority == "P1", "P1 con SLA diferentes")
        doc = docs[4]
        check(doc.raised_by == "solicitante@sanitas.example", "identidad del solicitante")
        check(doc.agent_group == "SANITAS Nivel 1", "ingreso a cola Nivel 1")
        frappe.set_user("solicitante2@sanitas.example")
        check(not frappe.has_permission("HD Ticket", "read", doc=doc), "aislamiento entre solicitantes")
        frappe.set_user("solicitante@sanitas.example")
        doc.priority = "P1"
        expect_denied(lambda: doc.save(), "solicitante no cambia prioridad interna")
        doc.reload()
        frappe.set_user("nivel1@sanitas.example")
        doc.check_permission("read")
        doc.new_comment("Diagnóstico sintético de Nivel 1")
        doc.reload()
        doc.reply_via_agent("Acuse de primera respuesta de demostración", to=doc.raised_by)
        doc.reload()
        check(bool(doc.first_responded_on), "primera respuesta registrada por agente")
        doc.custom_override_priority = 1
        doc.custom_override_reason = "Corrección sintética aprobada por agente"
        doc.priority = "P2"
        doc.status = "En atención Nivel 1"
        doc.save()
        doc.reload()
        check(doc.priority == "P2", "corrección manual justificada")
        check(bool(frappe.db.exists("Version", {"ref_doctype": "HD Ticket", "docname": doc.name})), "trazabilidad Version")
        doc.status = "Esperando usuario"
        doc.save()
        doc.reload()
        check(doc.status_category == "Paused" and bool(doc.on_hold_since), "SLA pausado esperando usuario")
        doc.status = "Escalado funcionalmente"
        doc.agent_group = "SANITAS Nivel 2"
        doc.save()
        doc.reload()
        doc.assign_agent("nivel2@sanitas.example")
        doc.reload()
        check(frappe.db.exists("ToDo", {"reference_type": "HD Ticket", "reference_name": doc.name,
              "allocated_to": "nivel2@sanitas.example", "status": "Open"}), "escalamiento y asignación Nivel 2")
        doc.resolution_details = "Solución sintética y procedimiento registrado"
        doc.status = "Resuelto"
        doc.save()
        doc.reload()
        check(doc.status_category == "Resolved" and bool(doc.resolution_date), "resolución registrada")
        check(doc.agreement_status == "Fulfilled", "cumplimiento SLA registrado")
        doc.status = "Closed"
        doc.custom_close_evidence = "Validación explícita ficticia del usuario"
        doc.save()
        doc.reload()
        check(doc.status == "Closed", "cierre con constancia")
        doc.status = "Registrado"
        doc.save()
        doc.reload()
        check(doc.status_category == "Open", "reapertura por soporte")
        security = ticket(p["sla"][-1])
        frappe.set_user("nivel1@sanitas.example")
        security.custom_security = 1
        security.save()
        security.reload()
        check(security.priority == "P1" and security.agent_group == "SANITAS Responsable"
              and security.status == "Escalado jerárquicamente" and security.sla == "SANITAS Seguridad",
              "seguridad P1 y escalamiento jerárquico")
        sla = frappe.get_doc("HD Service Level Agreement", docs[0].sla)
        check(sla.calc_elapsed_time("2026-10-23 20:00:00", "2026-10-26 08:30:00") == 3600,
              "cómputo de horario y fin de semana: una hora hábil")
        check(str(sla.calc_time("2026-10-23 20:20:00", "P1", "response_time")) == "2026-10-26 08:05:00",
              "vencimiento de respuesta atraviesa fin de semana")
        overdue = docs[0]
        overdue.service_level_agreement_creation = frappe.utils.add_days(frappe.utils.now_datetime(), -14)
        overdue.save()
        overdue.reload()
        check(overdue.agreement_status == "Failed", "incumplimiento SLA detectado con inicio sintético antiguo")
        print("PASS pruebas funcionales de servidor. Navegador, reinicio y restauración requieren evidencias separadas.")
    finally:
        frappe.set_user("Administrator")
        frappe.db.rollback(save_point="sanitas_validation")
        frappe.set_user(original_user)
