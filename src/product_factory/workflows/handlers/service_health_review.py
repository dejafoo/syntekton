"""Registered runtime behavior for the read-only service-health-review pack."""

from product_factory.workflows.default_plans import default_service_health_review_plan
from product_factory.workflows.handlers.operational import OperationalHandler


class ServiceHealthReviewHandler(OperationalHandler):
    pack_id = "service_health_review"
    record_type = "service_health_review"
    plan_factory = staticmethod(default_service_health_review_plan)


__all__ = ["ServiceHealthReviewHandler"]
