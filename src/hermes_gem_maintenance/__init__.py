"""Deterministic operations for maintaining genome-scale metabolic models.

The package decides what can be decided from the model and the request. It does not
guess biology: an ambiguous mapping raises rather than picking a candidate.
"""

from __future__ import annotations

from hermes_gem_maintenance.changes import ReactionRequest, add_reaction
from hermes_gem_maintenance.checks import (
    CheckResult,
    UncomparableGPRError,
    canonical_gpr,
    check_candidate,
    comparable_gpr,
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
    unique_match,
)
from hermes_gem_maintenance.model_io import (
    approved_digest,
    file_digest,
    load_model,
    staged_write,
    verify_digest,
    verify_source,
)
from hermes_gem_maintenance.publish import (
    CandidateResult,
    ExportResult,
    build_candidate,
    publish_deliverable,
)

__all__ = [
    "CandidateResult",
    "CheckResult",
    "ExportResult",
    "GemMaintenanceError",
    "InsufficientInformationError",
    "ModelIntegrityError",
    "ReactionRequest",
    "RequestViolationError",
    "UncomparableGPRError",
    "ValidationFailedError",
    "add_reaction",
    "approved_digest",
    "build_candidate",
    "canonical_gpr",
    "check_candidate",
    "comparable_gpr",
    "describe_metabolite",
    "describe_reaction",
    "diff_snapshots",
    "file_digest",
    "load_model",
    "publish_deliverable",
    "require_unique_metabolite",
    "resolve_metabolite",
    "semantic_snapshot",
    "staged_write",
    "summarize",
    "unique_match",
    "verify_digest",
    "verify_source",
]
