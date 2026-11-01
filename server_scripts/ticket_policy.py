# Frappe Server Script: DocType Event / HD Ticket / Before Validate.
# No imports: executed by Frappe safe_exec. Re-select native SLA after classification.
agent = frappe.session.user == "Administrator" or frappe.db.exists("HD Agent", {"user": frappe.session.user, "is_active": 1})
old = doc.get_doc_before_save()
if not agent:
    if doc.is_new():
        doc.raised_by = frappe.session.user
        doc.agent_group = "SANITAS Nivel 1"
        doc.status = "Nuevo"
        doc.custom_override_priority = 0
        doc.custom_override_reason = None
        doc.resolution_details = None
        doc.custom_maintenance_date = None
        doc.custom_close_evidence = None
        doc.custom_demo_id = None
        doc.custom_related_ticket = None
    else:
        if old.raised_by != frappe.session.user:
            frappe.throw("Solo puede modificar sus propios tickets")
        for field in ["raised_by", "priority", "agent_group", "custom_override_priority", "custom_override_reason", "resolution_details", "custom_maintenance_date", "custom_close_evidence", "custom_demo_id", "custom_related_ticket"]:
            if doc.get(field) != old.get(field):
                frappe.throw("Campo reservado para soporte: " + field)
        if old.custom_security and not doc.custom_security:
            frappe.throw("Solo soporte puede descartar un incidente de seguridad")
        if doc.status != old.status and doc.status not in ["Nuevo", "Closed"]:
            frappe.throw("Transición reservada para soporte")
        if doc.status == "Closed" and old.status != "Resuelto":
            frappe.throw("El ticket debe estar resuelto antes de validar su cierre")

if not doc.subject or not doc.description:
    frappe.throw("Título y descripción son obligatorios. Use únicamente datos sintéticos.")
if not doc.custom_requester_role or not doc.ticket_type or not doc.custom_module:
    frappe.throw("Indique rol, tipo de caso y módulo SANITAS")
matrix = {
    "Alto": {"Alta": "P1", "Media": "P1", "Baja": "P2"},
    "Medio": {"Alta": "P2", "Media": "P3", "Baja": "P3"},
    "Bajo": {"Alta": "P3", "Media": "P4", "Baja": "P4"}
}
if doc.custom_impact not in matrix or doc.custom_urgency not in ["Alta", "Media", "Baja"]:
    frappe.throw("Impacto y urgencia son obligatorios")
if doc.custom_security:
    doc.priority = "P1"
    if doc.is_new() or not old.custom_security:
        doc.status = "Escalado jerárquicamente"
        doc.agent_group = "SANITAS Responsable"
elif doc.custom_override_priority:
    # Customer replies retain an existing, protected agent override unchanged.
    if not doc.custom_override_reason or doc.priority not in ["P1", "P2", "P3", "P4"]:
        frappe.throw("La corrección manual requiere agente, prioridad válida y justificación")
else:
    doc.priority = matrix[doc.custom_impact][doc.custom_urgency]
doc.agent_group = doc.agent_group or "SANITAS Nivel 1"
doc.status_category = frappe.db.get_value("HD Ticket Status", doc.status, "category")
if agent and doc.status_category == "Resolved" and not doc.resolution_details:
    frappe.throw("Registre la resolución antes de resolver o cerrar")
if agent and doc.status == "Closed" and (not old or old.status != "Closed") and not doc.custom_close_evidence:
    frappe.throw("Documente validación del usuario o dos días hábiles sin respuesta")
doc.set_sla()
