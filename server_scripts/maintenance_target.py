# Frappe Server Script / HD Ticket / After Save.
# Frappe's "Before Save" script maps to validate, before Helpdesk.before_save
# recalculates SLA targets. Normalize after persistence, without another save.
if doc.sla == "SANITAS Bajo-Baja" and not doc.custom_security and doc.resolution_by:
    doc.db_set("resolution_by", None, update_modified=False)
