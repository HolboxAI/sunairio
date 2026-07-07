"""Access control from Metadata DB user_entities."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


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
