"""Access control from Metadata DB user_entities."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from security.sql_guard import extract_project_names


@dataclass
class UserACL:
    username: str
    entity_ids: List[str] = field(default_factory=list)
    project_names: List[str] = field(default_factory=list)
    timezone_by_project: Dict[str, str] = field(default_factory=dict)
    is_admin: bool = False

    def allows_entity(self, shortname: str) -> bool:
        if self.is_admin:
            return True
        return shortname in self.project_names

    def allows_project(self, project_name: str) -> bool:
        return self.allows_entity(project_name)


def validate_sql_acl(sql: str, acl: Optional[UserACL]) -> None:
    if acl is None or acl.is_admin:
        return
    for project in extract_project_names(sql):
        if not acl.allows_project(project):
            raise ValueError(f"Access denied for project '{project}'")
