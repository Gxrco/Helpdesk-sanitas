"""Configuration applied in a Frappe site context; this is not a Frappe app."""
import json
from pathlib import Path

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter
from frappe.permissions import add_permission, update_permission_property

ROOT = Path(__file__).resolve().parents[1]


def policy():
    return json.loads((ROOT / "config/policy.json").read_text())


def upsert(doctype, name, values):
    """Only managed records are replaced; unknown schema fields are errors."""
    meta = frappe.get_meta(doctype)
    unknown = set(values) - {f.fieldname for f in meta.fields}
    if unknown:
        raise RuntimeError(f"Incompatible schema {doctype}: {sorted(unknown)}")
    if frappe.db.exists(doctype, name):
        doc = frappe.get_doc(doctype, name)
        # Some native records (notably Closed) reject even an unchanged save.
        if all(doc.get(field) == value for field, value in values.items()):
            return doc
        doc.update(values)
        doc.save(ignore_permissions=True)
    else:
        doc = frappe.get_doc({"doctype": doctype, "name": name, **values})
        doc.insert(ignore_permissions=True)
    return doc


def custom_fields(p):
    fields = []
    for name, label, options in [
        ("custom_requester_role", "Rol del solicitante", p["requester_roles"]),
        ("custom_module", "Servicio o módulo SANITAS", p["modules"]),
        ("custom_impact", "Impacto", ["Alto", "Medio", "Bajo"]),
        ("custom_urgency", "Urgencia", ["Alta", "Media", "Baja"]),
    ]:
        fields.append(dict(fieldname=name, label=label, fieldtype="Select",
                           options="\n".join([""] + options), insert_after="ticket_type",
                           in_standard_filter=1, in_list_view=1, permlevel=0))
    fields.append(dict(fieldname="custom_security", label="Posible exposición de información",
                       fieldtype="Check", insert_after="custom_urgency", default="0",
                       description="Solo describa el riesgo; no adjunte expedientes ni datos médicos.", permlevel=0))
    for name, label, fieldtype, extra in [
        ("custom_override_priority", "Corregir prioridad manualmente", "Check", {"default": "0"}),
        ("custom_override_reason", "Justificación de prioridad", "Small Text", {}),
        ("custom_maintenance_date", "Próximo mantenimiento acordado", "Datetime", {}),
        ("custom_close_evidence", "Constancia de validación o espera hábil", "Small Text", {}),
        ("custom_related_ticket", "Incidente, problema o cambio relacionado", "Link", {"options": "HD Ticket"}),
        ("custom_demo_id", "Identificador de carga sintética", "Data", {"unique": 1, "read_only": 1, "no_copy": 1}),
    ]:
        fields.append(dict(fieldname=name, label=label, fieldtype=fieldtype,
                           insert_after="resolution_details", permlevel=8, **extra))
    create_custom_fields({"HD Ticket": fields}, update=True)
    make_property_setter("HD Ticket", None, "track_changes", 1, "Check", for_doctype=True)
    make_property_setter("HD Ticket", "description", "reqd", 1, "Check")
    make_property_setter("HD Ticket", "resolution_details", "permlevel", 8, "Int")
    for role in ["Agent", "Agent Manager", "System Manager"]:
        add_permission("HD Ticket", role, permlevel=8)
        for prop in ["read", "write"]:
            update_permission_property("HD Ticket", role, 8, prop, 1)
    frappe.clear_cache(doctype="HD Ticket")


def configure_portal():
    # The pinned release uses hide_from_customer; newer releases use visible_to.
    meta = frappe.get_meta("HD Ticket Template Field")
    fields = []
    public = ["ticket_type", "custom_requester_role", "custom_module", "custom_impact", "custom_urgency", "custom_security"]
    internal = ["custom_override_priority", "custom_override_reason", "custom_maintenance_date",
                "custom_close_evidence", "custom_related_ticket", "resolution_details"]
    for name in public + internal:
        row = dict(fieldname=name, required=int(name in public and name != "custom_security"))
        if meta.has_field("visible_to"):
            row["visible_to"] = "Everyone" if name in public else "Agents"
            if meta.has_field("editable_after_creation"):
                row["editable_after_creation"] = 0
        elif meta.has_field("hide_from_customer"):
            row["hide_from_customer"] = int(name in internal)
        else:
            raise RuntimeError("Unsupported portal template visibility schema")
        fields.append(row)
    upsert("HD Ticket Template", "Default", {
        "template_name": "Default", "fields": fields,
        "about": "<p>SANITAS Service Desk — Clínica UVG. Use solo datos sintéticos. No incluya nombres de pacientes, CUI, diagnósticos médicos ni expedientes.</p>",
    })


def configure_users(p):
    password = Path("/run/secrets/demo_password").read_text().strip()
    upsert("HD Customer", p["organization"], {"customer_name": p["organization"], "customer_type": "Company"})
    for user in p["users"]:
        email = user["email"]
        if not frappe.db.exists("User", email):
            doc = frappe.get_doc(dict(doctype="User", email=email, first_name=user["name"],
                                      enabled=1, user_type="Website User" if user["role"] == "HD Customer" else "System User",
                                      send_welcome_email=0, new_password=password,
                                      roles=[{"role": user["role"]}]))
            if user["role"] == "Agent Manager":
                doc.append("roles", {"role": "Agent"})
            doc.insert(ignore_permissions=True)
        if "team" in user:
            agent = frappe.db.get_value("HD Agent", {"user": email}, "name") or email
            upsert("HD Agent", agent, {"user": email, "agent_name": user["name"], "is_active": 1})
        else:
            contact = frappe.db.get_value("Contact", {"user": email}, "name")
            if not contact:
                frappe.get_doc(dict(doctype="Contact", first_name=user["name"], user=email,
                    email_ids=[{"email_id": email, "is_primary": 1}],
                    links=[{"link_doctype": "HD Customer", "link_name": p["organization"]}])).insert(ignore_permissions=True)
    for user in p["users"]:
        if "team" in user:
            upsert("HD Team", user["team"], {"team_name": user["team"], "users": [{"user": user["email"]}], "disabled": 0})


def configure_calendar(p):
    meta = frappe.get_meta("HD Service Holiday List")
    # Derive naming field from installed metadata, rather than guessing a field.
    naming_field = (meta.autoname or "").removeprefix("field:")
    values = {"from_date": "2026-01-01", "to_date": "2035-12-31", "holidays": p["holidays"]}
    if meta.has_field(naming_field):
        values[naming_field] = p["holiday_list"]
    elif meta.has_field("holiday_list_name"):
        values["holiday_list_name"] = p["holiday_list"]
    else:
        raise RuntimeError("Review HD Service Holiday List naming schema")
    upsert("HD Service Holiday List", p["holiday_list"], values)


def sla_values(p, row, condition):
    return dict(service_level=row["name"], enabled=1, default_sla=0,
        condition=condition, holiday_list=p["holiday_list"],
        apply_sla_for_resolution=int(row["resolution"] is not None),
        default_ticket_status="Nuevo", ticket_reopen_status="Registrado",
        priorities=[dict(priority=priority, response_time=row["response"],
                         resolution_time=row["resolution"] or 0,
                         default_priority=int(priority == row["priority"]))
                    for priority in ["P1", "P2", "P3", "P4"]],
        support_and_resolution=[dict(workday=d, start_time=p["start_time"], end_time=p["end_time"])
                                for d in p["workdays"]])


def configure_slas(p):
    # Dedicated SANITAS site: disable stock policies to prevent fallback mismatches.
    for name in frappe.get_all("HD Service Level Agreement", pluck="name"):
        if not name.startswith("SANITAS "):
            doc = frappe.get_doc("HD Service Level Agreement", name)
            doc.enabled = 0
            doc.default_sla = 0
            doc.save(ignore_permissions=True)
    for row in p["sla"]:
        row = dict(row, name=f'SANITAS {row["impact"]}-{row["urgency"]}')
        condition = f'not doc.custom_security and doc.custom_impact == {row["impact"]!r} and doc.custom_urgency == {row["urgency"]!r}'
        upsert("HD Service Level Agreement", row["name"], sla_values(p, row, condition))
    security = dict(name="SANITAS Seguridad", priority="P1", response=900, resolution=14400)
    upsert("HD Service Level Agreement", security["name"], sla_values(p, security, "doc.custom_security == 1"))


def configure_charts():
    charts = []
    for label, field in [("Tipo", "ticket_type"), ("Módulo", "custom_module"),
                         ("Prioridad", "priority"), ("Estado", "status"), ("SLA", "agreement_status")]:
        name = f"SANITAS - {label}"
        upsert("Dashboard Chart", name, dict(chart_name=name, chart_type="Group By",
            document_type="HD Ticket", group_by_type="Count", group_by_based_on=field,
            number_of_groups=20, type="Bar", is_public=0, timeseries=0,
            filters_json="[]", roles=[{"role": "Agent"}, {"role": "Agent Manager"}]))
        charts.append({"chart": name, "width": "Half"})
    upsert("Dashboard", "SANITAS Service Desk", dict(dashboard_name="SANITAS Service Desk", charts=charts))


def configure_statuses(p):
    for index, status in enumerate(p["statuses"], 1):
        color = "Green" if status["category"] == "Resolved" else "Blue"
        if status["name"] == "Closed":
            if not frappe.db.exists("HD Ticket Status", "Closed"):
                raise RuntimeError("Falta el estado nativo Closed; revise la instalación de Helpdesk")
            # Preserve all protected native fields, including the customer label.
            # upsert skips the save when color/order already match.
            values = dict(order=index, color=color)
        else:
            values = dict(label_agent=status["name"], category=status["category"],
                          enabled=1, order=index, color=color)
        upsert("HD Ticket Status", status["name"], values)


def configure_maintenance_targets():
    upsert("Server Script", "SANITAS - Mantenimiento sin plazo fijo", dict(
        script_type="DocType Event", reference_doctype="HD Ticket", doctype_event="After Save",
        disabled=0, script=(ROOT / "server_scripts/maintenance_target.py").read_text()))
    # Correct targets left by earlier configurations on existing tickets.
    # Keep their content, classification, SLA response target and modified timestamp.
    frappe.db.set_value("HD Ticket", {"sla": "SANITAS Bajo-Baja", "custom_security": 0},
                        "resolution_by", None, update_modified=False)


def apply():
    import helpdesk
    if helpdesk.__version__ != "1.31.0":
        raise RuntimeError(f"Expected Helpdesk 1.31.0; found {helpdesk.__version__}. Review before upgrading.")
    p = policy()
    frappe.set_user("Administrator")
    frappe.get_single("System Settings").update({"time_zone": p["timezone"], "language": "es"}).save()
    # Settings come from policy.json; otherwise the Desk setup wizard blocks every /app route.
    frappe.db.set_single_value("System Settings", "setup_complete", 1)
    frappe.db.set_value("Installed Application", {"app_name": ["in", ["frappe", "telephony", "helpdesk"]]},
                        "is_setup_complete", 1)
    # As the wizard does on completion; while "setup-wizard", Desk renders no navbar.
    frappe.db.set_default("desktop:home_page", "workspace")
    custom_fields(p)
    for priority, level in zip(["P1", "P2", "P3", "P4"], ["Urgent", "High", "Medium", "Low"]):
        upsert("HD Ticket Priority", priority, {"level": level, "disabled": 0})
    for ticket_type in p["ticket_types"]:
        upsert("HD Ticket Type", ticket_type, {"description": ticket_type + " SANITAS", "disabled": 0})
    configure_statuses(p)
    configure_users(p)
    settings = frappe.get_single("HD Settings")
    settings.update(dict(brand_name=p["brand"], default_priority="P3", default_ticket_type="Incidente",
        default_ticket_status="Nuevo", ticket_reopen_status="Registrado", setup_complete=1,
        skip_email_workflow=1, send_acknowledgement_email=0, auto_close_tickets=0,
        auto_update_status=0, allow_anyone_to_create_tickets=0,
        restrict_tickets_by_agent_group=0, assign_within_team=1))
    settings.save()
    configure_portal()
    configure_calendar(p)
    configure_slas(p)
    upsert("Server Script", "SANITAS - Política de tickets", dict(
        script_type="DocType Event", reference_doctype="HD Ticket", doctype_event="Before Validate",
        disabled=0, script=(ROOT / "server_scripts/ticket_policy.py").read_text()))
    configure_maintenance_targets()
    configure_charts()
    frappe.db.commit()
    frappe.clear_cache()
    print("Configuración SANITAS aplicada. Ejecute validate y pruebas de navegador.")


def demo():
    frappe.set_user("Administrator")
    p = policy()
    rows = json.loads((ROOT / "config/demo.json").read_text())
    created = 0
    for row in rows:
        if frappe.db.exists("HD Ticket", {"custom_demo_id": row["id"]}):
            continue
        doc = frappe.get_doc(dict(doctype="HD Ticket", subject=row["subject"],
            description="<p>DEMO SINTÉTICA. " + row["description"] + " No contiene datos médicos reales.</p>",
            raised_by="solicitante@sanitas.example", customer=p["organization"],
            ticket_type=row["type"], custom_requester_role="Recepción", custom_module=p["modules"][row["module"]],
            custom_impact=row["impact"], custom_urgency=row["urgency"], custom_security=row.get("security", 0),
            custom_demo_id=row["id"], agent_group=row["team"]))
        doc.insert(ignore_permissions=True)
        # after_insert creates a Communication whose hook saves a separate
        # HD Ticket instance. Refresh before replying or editing this instance.
        doc.reload()
        agent = next(u["email"] for u in p["users"] if u.get("team") == row["team"])
        if row["status"] not in ["Nuevo", "Registrado", "En clasificación"]:
            frappe.set_user(agent)
            doc.reply_via_agent("DEMO: caso recibido; seguimiento mediante el portal.", to=doc.raised_by)
            doc.reload()
            frappe.set_user("Administrator")
        doc.status = row["status"]
        doc.status_category = None
        if row["status"] in ["Resuelto", "Closed"]:
            doc.resolution_details = "Solución teórica documentada y validada en el escenario sintético."
        if row["status"] == "Closed":
            doc.custom_close_evidence = "Validación explícita del solicitante ficticio en la demostración."
        doc.save(ignore_permissions=True)
        doc.reload()
        doc.assign_agent(agent)
        doc.reload()
        frappe.set_user(agent)
        doc.new_comment("DEMO: " + row["comment"])
        frappe.set_user("Administrator")
        created += 1
    # Link the three recurring export incidents to their problem, using native Link fields.
    problem = frappe.db.get_value("HD Ticket", {"custom_demo_id": "SAN-DEMO-014"}, "name")
    for demo_id in ["SAN-DEMO-005", "SAN-DEMO-012", "SAN-DEMO-013"]:
        name = frappe.db.get_value("HD Ticket", {"custom_demo_id": demo_id}, "name")
        doc = frappe.get_doc("HD Ticket", name)
        if not doc.custom_related_ticket:
            doc.custom_related_ticket = problem
            doc.save(ignore_permissions=True)
    frappe.db.commit()
    print(f"Demo: {created} creados; {len(rows) - created} existentes conservados.")
