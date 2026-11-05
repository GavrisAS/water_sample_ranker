"""Typed boundaries for stage B. Missing sources cannot produce negative evidence."""

from datetime import date
from typing import Protocol

from water_sample_ranker.domain.models import CriterionResult, TargetIdentity


class ChokePort(Protocol):
    def evaluate(self, identity: TargetIdentity, as_of: date) -> CriterionResult: ...


class GtmPlanPort(Protocol):
    def evaluate(self, identity: TargetIdentity, as_of: date) -> CriterionResult: ...


class NeighborPort(Protocol):
    def evaluate(self, identity: TargetIdentity, as_of: date) -> CriterionResult: ...
