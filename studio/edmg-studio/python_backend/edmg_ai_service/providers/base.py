from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from ..schemas import PlanRequest, PlanResponse


class PlanProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    def model(self) -> Optional[str]:
        return None

    @abstractmethod
    def plan(self, req: PlanRequest) -> PlanResponse:
        ...
