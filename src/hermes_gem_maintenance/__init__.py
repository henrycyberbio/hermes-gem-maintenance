"""Deterministic operations for maintaining genome-scale metabolic models.

The package decides what can be decided from the model and the request. It does not
guess biology: an ambiguous mapping raises rather than picking a candidate.
"""

from __future__ import annotations

from hermes_gem_maintenance.changes import ReactionRequest, add_reaction
from hermes_gem_maintenance.checks import (
    CheckResult,
    canonical_gpr,
    check_candidate,
    diff_snapshots,
    semantic_snapshot,
)
from hermes_gem_maintenance.errors import (
    GemMaintenanceError,
    InsufficientInformationError,
    ModelIntegrityError,
    RequestViolationError,
    ValidationFailedError,
)
from hermes_gem_maintenance.inspect import (
    describe_metabolite,
    describe_reaction,
    require_unique_metabolite,
    resolve_metabolite,
    summarize,
)
from hermes_gem_maintenance.model_io import (
    file_digest,
    load_model,
    save_candidate,
    verify_digest,
)

__all__ = [
    "CheckResult",
    "GemMaintenanceError",
    "InsufficientInformationError",
    "ModelIntegrityError",
    "ReactionRequest",
    "RequestViolationError",
    "ValidationFailedError",
    "add_reaction",
    "canonical_gpr",
    "check_candidate",
    "describe_metabolite",
    "describe_reaction",
    "diff_snapshots",
    "file_digest",
    "load_model",
    "require_unique_metabolite",
    "resolve_metabolite",
    "save_candidate",
    "semantic_snapshot",
    "summarize",
    "verify_digest",
]
