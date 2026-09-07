"""Producing candidates and publishing deliverables.

The operations here are the only sanctioned way to turn a request into a file on
disk. They exist as library functions, not CLI internals, so that a Python caller and
the command line get the same guarantees: the baseline is the approved artifact, the
bytes that will be published are the bytes that were checked, and the destination is
never overwritten.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from cobra.io import write_sbml_model

from hermes_gem_maintenance.changes import add_reaction
from hermes_gem_maintenance.checks import (
    CheckResult,
    check_candidate,
    diff_snapshots,
    semantic_snapshot,
)
from hermes_gem_maintenance.errors import ValidationFailedError
from hermes_gem_maintenance.model_io import (
    file_digest,
    load_model,
    staged_write,
    verify_digest,
    verify_source,
)

if TYPE_CHECKING:
    from pathlib import Path

    from hermes_gem_maintenance.changes import ReactionRequest


# ==== results ====


@dataclass(frozen=True)
class CandidateResult:
    """What `build_candidate` wrote and what it verified while writing."""

    reaction_id: str
    candidate: Path
    candidate_sha256: str
    baseline_sha256: str
    baseline_verified_against: str

    def as_dict(self) -> dict[str, Any]:
        """Structured form for JSON output."""
        return {
            "reaction_id": self.reaction_id,
            "candidate": self.candidate.name,
            "candidate_sha256": self.candidate_sha256,
            "baseline_sha256": self.baseline_sha256,
            "baseline_verified_against": self.baseline_verified_against,
        }


@dataclass(frozen=True)
class ExportResult:
    """What `publish_deliverable` released, and the checks that permitted it."""

    reaction_id: str
    delivered: Path
    delivered_sha256: str
    baseline_verified_against: str
    checks: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        """Structured form for JSON output."""
        return {
            "reaction_id": self.reaction_id,
            "delivered": self.delivered.name,
            "delivered_sha256": self.delivered_sha256,
            "baseline_verified_against": self.baseline_verified_against,
            **self.checks,
        }


def provenance_label(manifest: Path | None, expected: str) -> str:
    """State which guarantee the baseline check actually gave.

    "Unchanged during this command" and "is the approved artifact" are different
    claims, and a payload that does not distinguish them invites the weaker one to be
    read as the stronger.
    """
    if manifest is not None:
        return f"source manifest: {manifest.name}"
    if expected:
        return "caller-supplied expected_sha256"
    return "self-digest only: unchanged during this command, provenance unverified"


# ==== operations ====


def build_candidate(
    baseline: Path,
    request: ReactionRequest,
    destination: Path,
    *,
    manifest: Path | None = None,
    expected_sha256: str = "",
) -> CandidateResult:
    """Apply a request to the baseline and stage the result at `destination`.

    The staged file is read back before it is published: a candidate is the input to
    every later step, so hashing bytes without confirming they parse would hand the
    caller a digest for something that is not a model.
    """
    before = verify_source(baseline, manifest=manifest, expected=expected_sha256)
    model = load_model(baseline)
    add_reaction(model, request)

    with staged_write(destination, protected=baseline) as staged:
        write_sbml_model(model, str(staged))
        load_model(staged)
        verify_digest(baseline, before)
        digest = file_digest(staged)

    return CandidateResult(
        reaction_id=request.reaction_id,
        candidate=destination,
        candidate_sha256=digest,
        baseline_sha256=before,
        baseline_verified_against=provenance_label(manifest, expected_sha256),
    )


def publish_deliverable(
    baseline: Path,
    candidate: Path,
    request: ReactionRequest,
    destination: Path,
    *,
    manifest: Path | None = None,
    expected_sha256: str = "",
    candidate_sha256: str = "",
) -> ExportResult:
    """Re-check a candidate and publish it only if the published bytes are sound.

    Both inputs are pinned by digest for the duration of the call, and the candidate
    is parsed once: comparing a file that was re-read between checks would validate
    bytes other than the ones being published. Pass `candidate_sha256` from
    `build_candidate` to also pin the gap between the two commands.

    The staged file is loaded back and checked rather than merely written and hashed.
    Existing on disk with a SHA-256 is not evidence of being a valid model, so
    everything after the write is verified against the artifact that will be released.
    """
    before = verify_source(baseline, manifest=manifest, expected=expected_sha256)
    candidate_before = verify_source(
        candidate, manifest=None, expected=candidate_sha256
    )

    base_model = load_model(baseline)
    proposed = load_model(candidate)
    proposed_state = semantic_snapshot(proposed)
    _refuse_unless_passed(
        check_candidate(base_model, proposed, request), "candidate"
    )

    with staged_write(destination, protected=baseline) as staged:
        write_sbml_model(proposed, str(staged))

        # Reading the staged file back is the point: a writer that returns cleanly
        # having emitted unusable bytes would otherwise be published with a passing
        # verdict. load_model reports an unreadable file as `model_integrity`.
        published = load_model(staged)
        staged_result = check_candidate(base_model, published, request)
        _refuse_unless_passed(staged_result, "staged deliverable")

        # Catches loss in the SBML writer itself: the checks above confirm the
        # requested reaction survived, this confirms nothing else was dropped on the
        # way out.
        drift = diff_snapshots(proposed_state, semantic_snapshot(published))
        if drift:
            msg = "staged deliverable differs from the candidate; nothing published"
            raise ValidationFailedError(msg, drift=drift, **staged_result.as_dict())

        verify_digest(baseline, before)
        verify_digest(candidate, candidate_before)
        digest = file_digest(staged)

    return ExportResult(
        reaction_id=request.reaction_id,
        delivered=destination,
        delivered_sha256=digest,
        baseline_verified_against=provenance_label(manifest, expected_sha256),
        checks=staged_result.as_dict(),
    )


def _refuse_unless_passed(result: CheckResult, subject: str) -> None:
    """Raise unless every check ran and passed, naming which artifact failed."""
    if result.ok:
        return
    msg = (
        f"{subject} failed re-validation; no deliverable written"
        if result.blocked
        else f"{subject} could not be fully verified; no deliverable written"
    )
    raise ValidationFailedError(msg, **result.as_dict())
