"""Registered runtime behavior for the read-only incident-triage pack."""

from product_factory.workflows.default_plans import default_incident_triage_plan
from product_factory.workflows.handlers.operational import OperationalHandler


class IncidentTriageHandler(OperationalHandler):
    pack_id = "incident_triage"
    record_type = "incident_triage"
    plan_factory = staticmethod(default_incident_triage_plan)


__all__ = ["IncidentTriageHandler"]
