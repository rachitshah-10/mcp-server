from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

from it_equipment_handler.db import EquipmentRepository

# Map eligibility status codes to relevant natural-language policy clause topics.
CLAUSE_TOPICS_BY_STATUS: dict[str, set[str]] = {
    "ELIGIBLE": {"conflicting_facts", "reviewer_directory"},
    "INELIGIBLE_STATUS": {"lifecycle_freeze"},
    "INELIGIBLE_COOLDOWN": {"hardware_damage", "lost_or_stolen", "ergonomic_medical", "reviewer_directory"},
    "INELIGIBLE_ROLE": {"ergonomic_medical", "reviewer_directory"},
    "OPEN_TICKET_EXISTS": {"duplicate_tickets", "reviewer_directory"},
    "REQUIRES_MANAGER_APPROVAL": {"reviewer_directory"},
    "INSUFFICIENT_DEPARTMENT_BUDGET": {"annual_budget", "reviewer_directory"},
    "UNKNOWN_ITEM": {"ergonomic_medical", "reviewer_directory"},
}


def _calculate_months_elapsed(start_date_str: str, ref_date: date) -> int:
    """Compute whole months elapsed from an ISO date string to ref_date."""
    start_date = date.fromisoformat(start_date_str)
    months = (ref_date.year - start_date.year) * 12 + (ref_date.month - start_date.month)
    if ref_date.day < start_date.day:
        months -= 1
    return max(0, months)


def _require_non_blank(value: str, field_name: str) -> str:
    """Strip a string input and raise ValueError if blank."""
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} is mandatory and cannot be blank.")
    return cleaned


def _score_resource_match(query_lower: str, resource: dict[str, Any]) -> int:
    """Score how well a resource matches a user item query (higher is better, 0 means no match)."""
    name_lower = resource["name"].lower()
    category_lower = resource["category"].lower()
    aliases = [alias.strip().lower() for alias in resource["aliases"].split(",") if alias.strip()]

    if query_lower == resource["resource_id"].lower() or query_lower == name_lower:
        return 100
    if query_lower in aliases:
        return 90
    if query_lower == category_lower:
        # Prefer standard tier when a broad category name like "laptop" matches multiple tiers.
        return 80 if resource["tier"] == "standard" else 70
    if any(alias in query_lower or query_lower in alias for alias in aliases):
        return 60 if resource["tier"] == "standard" else 55
    if query_lower in name_lower:
        return 50
    return 0


def _select_policy(
    policies: list[dict[str, Any]],
    employment_type: str,
    work_mode: str,
    category: str,
) -> tuple[dict[str, Any] | None, str]:
    """Select the most specific structured policy and classify any mismatch reason."""
    role_matches = [
        p for p in policies
        if p["employment_type"] in (employment_type, "any")
        and p["category"] in (category, "any")
    ]
    if not role_matches:
        return None, "INELIGIBLE_ROLE"

    # Prefer category-specific rows over wildcard category='any' rows.
    exact_category = [p for p in role_matches if p["category"] == category]
    candidates = exact_category or role_matches

    for policy in candidates:
        allowed_modes = [m.strip() for m in policy["work_mode"].split(",")]
        if work_mode in allowed_modes:
            if not policy["allowed"]:
                reason = "INELIGIBLE_WORK_MODE" if policy["work_mode"] != "any" else "INELIGIBLE_ROLE"
                return policy, reason
            return policy, "ALLOWED"

    for policy in candidates:
        if policy["work_mode"] == "any":
            if not policy["allowed"]:
                return policy, "INELIGIBLE_ROLE"
            return policy, "ALLOWED"

    return None, "INELIGIBLE_WORK_MODE"


class EquipmentService:
    """Core business operations for employee, policy, eligibility, and ticket workflows."""

    def __init__(self, repo: EquipmentRepository) -> None:
        """Initialize the service with a repository abstraction."""
        self._repo = repo

    def _eligibility_result(
        self,
        *,
        eligible: bool,
        status_code: str,
        employee_id: str,
        item: str,
        reason: str,
        **extra: Any,
    ) -> dict[str, Any]:
        """Build a consistent eligibility response enriched with relevant policy clauses."""
        topics = CLAUSE_TOPICS_BY_STATUS.get(status_code, set())
        clauses = (
            [c for c in self._repo.get_policy_clauses() if c["topic"] in topics]
            if topics
            else []
        )
        payload: dict[str, Any] = {
            "eligible": eligible,
            "status_code": status_code,
            "employee_id": employee_id,
            "item": item,
            "reason": reason,
            "applicable_clauses": clauses,
        }
        payload.update(extra)
        return payload

    def get_employee_info(self, employee_id: str, as_of: date | None = None) -> dict[str, Any]:
        """Return employee profile, tenure, dispatch history, tickets, and department budget."""
        normalized_id = _require_non_blank(employee_id, "employee_id")
        employee = self._repo.get_employee(normalized_id)
        if employee is None:
            return {
                "found": False,
                "employee_id": normalized_id,
                "message": f"Employee '{normalized_id}' was not found in the directory.",
            }

        ref_date = as_of or date.today()
        dispatches = self._repo.get_employee_dispatches(normalized_id)
        tickets = self._repo.get_employee_tickets(normalized_id)
        budget = self._repo.get_department_annual_budget(employee["department"], ref_date.year)

        return {
            "found": True,
            "employee_id": employee["employee_id"],
            "name": employee["name"],
            "role": employee["role"],
            "department": employee["department"],
            "employment_type": employee["employment_type"],
            "work_mode": employee["work_mode"],
            "status": employee["status"],
            "hire_date": employee["hire_date"],
            "tenure_months": _calculate_months_elapsed(employee["hire_date"], ref_date),
            "manager_id": employee["manager_id"],
            "dispatch_history": dispatches,
            "tickets": tickets,
            "department_budget": budget,
        }

    def get_policy_limits(self, role: str) -> dict[str, Any]:
        """Return structured policy limits for a role alongside natural-language policy clauses."""
        normalized_role = _require_non_blank(role, "role").lower()
        policies = self._repo.get_structured_policies(normalized_role)
        clauses = self._repo.get_policy_clauses()
        if not policies:
            return {
                "found": False,
                "role": normalized_role,
                "structured_policies": [],
                "policy_clauses": clauses,
                "message": f"Policy limits for role '{normalized_role}' were not found.",
            }

        return {
            "found": True,
            "role": normalized_role,
            "structured_policies": policies,
            "policy_clauses": clauses,
        }

    def lookup_resource(self, query: str) -> dict[str, Any]:
        """Resolve a product name, category, or alias against the resource catalog."""
        cleaned_query = _require_non_blank(query, "query")
        query_lower = cleaned_query.lower()

        scored: list[tuple[int, dict[str, Any]]] = []
        for resource in self._repo.search_resources(cleaned_query):
            score = _score_resource_match(query_lower, resource)
            if score > 0:
                scored.append((score, resource))

        if not scored:
            return {
                "found": False,
                "query": cleaned_query,
                "primary_match": None,
                "matches": [],
            }

        scored.sort(key=lambda pair: (-pair[0], pair[1]["unit_cost"]))
        matches = [item for _, item in scored]
        return {
            "found": True,
            "query": cleaned_query,
            "primary_match": matches[0],
            "matches": matches,
        }

    def check_request_eligibility(
        self,
        employee_id: str,
        item: str,
        as_of: date | None = None,
    ) -> dict[str, Any]:
        """Evaluate eligibility lazily so early denials skip dispatch, ticket, and budget queries."""
        normalized_id = _require_non_blank(employee_id, "employee_id")
        cleaned_item = _require_non_blank(item, "item")
        ref_date = as_of or date.today()

        employee = self._repo.get_employee(normalized_id)
        if employee is None:
            return self._eligibility_result(
                eligible=False,
                status_code="EMPLOYEE_NOT_FOUND",
                employee_id=normalized_id,
                item=cleaned_item,
                reason=f"Employee '{normalized_id}' was not found in the directory.",
            )

        if employee["status"] in ("notice_period", "offboarding"):
            return self._eligibility_result(
                eligible=False,
                status_code="INELIGIBLE_STATUS",
                employee_id=normalized_id,
                item=cleaned_item,
                reason=f"Employee status is '{employee['status']}'; hardware dispatches are frozen.",
            )

        lookup = self.lookup_resource(cleaned_item)
        if not lookup["found"]:
            return self._eligibility_result(
                eligible=False,
                status_code="UNKNOWN_ITEM",
                employee_id=normalized_id,
                item=cleaned_item,
                reason=f"Item '{cleaned_item}' could not be matched to any catalog resource.",
            )

        resource = lookup["primary_match"]
        category = resource["category"]
        policies = self._repo.get_structured_policies(employee["role"])
        policy, policy_status = _select_policy(
            policies,
            employee["employment_type"],
            employee["work_mode"],
            category,
        )
        if policy_status != "ALLOWED" or policy is None:
            return self._eligibility_result(
                eligible=False,
                status_code=policy_status,
                employee_id=normalized_id,
                item=cleaned_item,
                resource=resource,
                reason=f"Role '{employee['role']}' ({employee['work_mode']}) is not permitted '{category}' ({policy_status}).",
            )

        tenure_months = _calculate_months_elapsed(employee["hire_date"], ref_date)
        if tenure_months < policy["min_tenure_months"]:
            return self._eligibility_result(
                eligible=False,
                status_code="INELIGIBLE_TENURE",
                employee_id=normalized_id,
                item=cleaned_item,
                resource=resource,
                reason=f"Minimum tenure of {policy['min_tenure_months']} months not met (current: {tenure_months}).",
            )

        # Fetch history, tickets, and budget only after cheap employee/policy checks pass.
        dispatches = self._repo.get_employee_dispatches(normalized_id)
        active_dispatches = [d for d in dispatches if d["condition_status"] == "active"]
        required_cat = resource["requires_category"]
        if required_cat and not any(d["category"] == required_cat for d in active_dispatches):
            return self._eligibility_result(
                eligible=False,
                status_code="MISSING_DEPENDENCY",
                employee_id=normalized_id,
                item=cleaned_item,
                resource=resource,
                reason=f"'{resource['name']}' requires an active '{required_cat}' on file.",
            )

        tickets = self._repo.get_employee_tickets(normalized_id)
        open_tickets = [
            t for t in tickets
            if t["status"] in ("open", "escalated") and t["category"] == category
        ]
        if open_tickets:
            return self._eligibility_result(
                eligible=False,
                status_code="OPEN_TICKET_EXISTS",
                employee_id=normalized_id,
                item=cleaned_item,
                resource=resource,
                existing_tickets=open_tickets,
                reason=f"Employee already has an active ticket ({open_tickets[0]['ticket_id']}) for '{category}'.",
            )

        # Allow replacement once active items have reached the policy refresh window.
        active_in_cat = [d for d in active_dispatches if d["category"] == category]
        if len(active_in_cat) >= policy["max_active_qty"]:
            recent_months = min(
                _calculate_months_elapsed(d["dispatched_at"], ref_date) for d in active_in_cat
            )
            if recent_months < policy["refresh_months"]:
                remaining_months = policy["refresh_months"] - recent_months
                return self._eligibility_result(
                    eligible=False,
                    status_code="INELIGIBLE_COOLDOWN",
                    employee_id=normalized_id,
                    item=cleaned_item,
                    resource=resource,
                    months_since_last_issue=recent_months,
                    refresh_months=policy["refresh_months"],
                    reason=(
                        f"Employee already holds {len(active_in_cat)} active '{category}' "
                        f"issued {recent_months} months ago ({remaining_months} months left in refresh window)."
                    ),
                )

        if resource["unit_cost"] > policy["max_auto_cost_usd"]:
            return self._eligibility_result(
                eligible=False,
                status_code="REQUIRES_MANAGER_APPROVAL",
                employee_id=normalized_id,
                item=cleaned_item,
                resource=resource,
                manager_id=employee["manager_id"],
                reason=(
                    f"Item cost (${resource['unit_cost']:.2f}) exceeds auto-approval cap "
                    f"(${policy['max_auto_cost_usd']:.2f}); manager approval required."
                ),
            )

        budget = self._repo.get_department_annual_budget(employee["department"], ref_date.year)
        if budget is not None and budget["remaining_budget_usd"] < resource["unit_cost"]:
            return self._eligibility_result(
                eligible=False,
                status_code="INSUFFICIENT_DEPARTMENT_BUDGET",
                employee_id=normalized_id,
                item=cleaned_item,
                resource=resource,
                department_budget=budget,
                manager_id=employee["manager_id"],
                reason=(
                    f"Department '{budget['department']}' remaining annual budget "
                    f"(${budget['remaining_budget_usd']:.2f}) is below item cost (${resource['unit_cost']:.2f})."
                ),
            )

        return self._eligibility_result(
            eligible=True,
            status_code="ELIGIBLE",
            employee_id=normalized_id,
            item=cleaned_item,
            resource=resource,
            department_budget=budget,
            reason=f"Request for '{resource['name']}' satisfies all structured policy and budget limits.",
        )

    def flag_for_human_review(
        self,
        employee_id: str,
        request: str,
        reason: str,
        reviewer_employee_id: str,
        category: str | None = None,
        resource_id: str | None = None,
    ) -> dict[str, Any]:
        """Create an escalated human-review ticket assigned to the chosen reviewer."""
        norm_emp_id = _require_non_blank(employee_id, "employee_id")
        norm_req = _require_non_blank(request, "request")
        norm_reason = _require_non_blank(reason, "reason")
        norm_reviewer_id = _require_non_blank(reviewer_employee_id, "reviewer_employee_id")

        if self._repo.get_employee(norm_emp_id) is None:
            raise ValueError(f"Employee '{norm_emp_id}' does not exist.")
        if self._repo.get_employee(norm_reviewer_id) is None:
            raise ValueError(f"Reviewer employee '{norm_reviewer_id}' does not exist.")

        resolved_category = category.strip().lower() if category and category.strip() else None
        resolved_resource_id = resource_id.strip() if resource_id and resource_id.strip() else None
        if resolved_resource_id is not None:
            resource = self._repo.get_resource(resolved_resource_id)
            if resource is None:
                raise ValueError(f"Resource '{resolved_resource_id}' does not exist.")
            resolved_category = resolved_category or resource["category"]
        elif resolved_category is None:
            # Infer category/resource when request text clearly references a catalog item.
            inferred = self.lookup_resource(norm_req)
            if inferred["found"]:
                resolved_category = inferred["primary_match"]["category"]
                resolved_resource_id = inferred["primary_match"]["resource_id"]

        ticket_id = f"TKT-{uuid4().hex[:8].upper()}"
        created_at = datetime.now(UTC).isoformat(timespec="seconds")
        return self._repo.create_ticket(
            ticket_id=ticket_id,
            employee_id=norm_emp_id,
            request_text=norm_req,
            category=resolved_category,
            resource_id=resolved_resource_id,
            status="escalated",
            flag_for_human_review=True,
            reviewer_employee_id=norm_reviewer_id,
            reason=norm_reason,
            created_at=created_at,
        )

    def finalize_request_decision(
        self,
        employee_id: str,
        request_text: str,
        decision: str,
        reason: str,
        resource_id: str | None = None,
    ) -> dict[str, Any]:
        """Log a finalized APPROVED/DENIED decision and atomically auto-dispatch approved items."""
        norm_emp_id = _require_non_blank(employee_id, "employee_id")
        norm_req = _require_non_blank(request_text, "request_text")
        norm_decision = _require_non_blank(decision, "decision").upper()
        norm_reason = _require_non_blank(reason, "reason")

        if norm_decision not in ("APPROVED", "DENIED"):
            raise ValueError("decision must be 'APPROVED' or 'DENIED' (escalations use flag_for_human_review).")

        log_id = f"LOG-{uuid4().hex[:8].upper()}"
        created_at = datetime.now(UTC).isoformat(timespec="seconds")

        if norm_decision == "APPROVED":
            if resource_id is None:
                raise ValueError("resource_id is mandatory when decision is 'APPROVED'.")
            norm_res_id = _require_non_blank(resource_id, "resource_id")
            if self._repo.get_employee(norm_emp_id) is None:
                raise ValueError(f"Cannot dispatch to nonexistent employee '{norm_emp_id}'.")
            if self._repo.get_resource(norm_res_id) is None:
                raise ValueError(f"Resource '{norm_res_id}' does not exist.")

            log_record, dispatch_record = self._repo.record_approval_and_dispatch(
                log_id=log_id,
                dispatch_id=f"DSP-{uuid4().hex[:8].upper()}",
                employee_id=norm_emp_id,
                resource_id=norm_res_id,
                serial_id=f"SN-{uuid4().hex[:8].upper()}",
                dispatched_at=date.today().isoformat(),
                request_text=norm_req,
                reason=norm_reason,
                created_at=created_at,
            )
            return {
                "log_id": log_record["log_id"],
                "employee_id": norm_emp_id,
                "decision": norm_decision,
                "reason": norm_reason,
                "dispatched": True,
                "dispatch_id": dispatch_record["dispatch_id"],
                "serial_id": dispatch_record["serial_id"],
            }

        log_record = self._repo.create_log(
            log_id=log_id,
            employee_id=norm_emp_id,
            request_text=norm_req,
            decision=norm_decision,
            reason=norm_reason,
            created_at=created_at,
        )
        return {
            "log_id": log_record["log_id"],
            "employee_id": norm_emp_id,
            "decision": norm_decision,
            "reason": norm_reason,
            "dispatched": False,
            "dispatch_id": None,
            "serial_id": None,
        }
