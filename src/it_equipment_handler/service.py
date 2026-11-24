from datetime import date
from typing import Any

from it_equipment_handler.db import EquipmentRepository


def _calculate_tenure_months(hire_date_str: str, ref_date: date) -> int:
    """Compute approximate whole months elapsed since hire_date."""
    hire_date = date.fromisoformat(hire_date_str)
    months = (ref_date.year - hire_date.year) * 12 + (ref_date.month - hire_date.month)
    if ref_date.day < hire_date.day:
        months -= 1
    return max(0, months)


class EquipmentService:
    """Core business operations for employee, policy, eligibility, and ticket workflows."""

    def __init__(self, repo: EquipmentRepository) -> None:
        """Initialize the service with a repository abstraction."""
        self._repo = repo

    def get_employee_info(self, employee_id: str, as_of: date | None = None) -> dict[str, Any]:
        """Return employee profile, tenure, dispatch history, tickets, and department budget."""
        normalized_id = employee_id.strip()
        if not normalized_id:
            raise ValueError("employee_id is mandatory and cannot be blank.")

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
            "tenure_months": _calculate_tenure_months(employee["hire_date"], ref_date),
            "manager_id": employee["manager_id"],
            "dispatch_history": dispatches,
            "tickets": tickets,
            "department_budget": budget,
        }
