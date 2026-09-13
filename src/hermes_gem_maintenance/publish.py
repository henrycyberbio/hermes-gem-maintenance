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

from hermes_gem_maintenance.changes import apply_changeset
from hermes_gem_maintenance.checks import (
    CheckResult,
    check_candidate,
    diff_snapshots,
    semantic_snapshot,
)
from hermes_gem_maintenance.consistency_review import (
    ConsistencyRegression,
    review_consistency,
)
from hermes_gem_maintenance.errors import ValidationFailedError
from hermes_gem_maintenance.model_io import (
    file_digest,
    load_model,
    staged_write,
    verify_digest,
    verify_output_paths,
    verify_source,
    write_json_artifact,
)

if TYPE_CHECKING:
    from pathlib import Path

    from hermes_gem_maintenance.changes import DeleteReactionRequest, ReactionRequest


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
    consistency_regression: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        """Structured form for JSON output.

        `scope` is set to "export" last, after `**self.checks`, so it overrides the
        "structural" value `CheckResult.as_dict()` always reports -- the checks in
        `self.checks` only covered layers 1-2 when they ran, but this payload as a
        whole also reflects the MEMOTE comparison in `consistency_regression`.
        """
        return {
            "reaction_id": self.reaction_id,
            "delivered": self.delivered.name,
            "delivered_sha256": self.delivered_sha256,
            "baseline_verified_against": self.baseline_verified_against,
            "consistency_regression": self.consistency_regression,
            **self.checks,
            "scope": "export",
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
    request: ReactionRequest | DeleteReactionRequest,
    destination: Path,
    *,
    manifest: Path | None = None,
    expected_sha256: str = "",
) -> CandidateResult:
    """Apply a request to the baseline and stage the result at `destination`.

    The staged file is read back before it is published. A candidate is the input to
    every later step, so writing bytes and hashing them without confirming they parse
    would hand the caller a digest for something that is not a model -- the same
    mistake `publish_deliverable` exists to prevent, one stage earlier.
    """
    before = verify_source(baseline, manifest=manifest, expected=expected_sha256)
    model = load_model(baseline)
    apply_changeset(model, request)

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
    request: ReactionRequest | DeleteReactionRequest,
    destination: Path,
    *,
    manifest: Path | None = None,
    expected_sha256: str = "",
    record_directory: Path | None = None,
) -> ExportResult:
    """Re-check a candidate and publish it only if the published bytes are sound.

    The staged file is loaded back and checked, not merely written and hashed. A
    writer that returns cleanly having produced unreadable output would otherwise
    publish it with `status: "passed"` -- existing on disk and having a SHA-256 is not
    evidence of being a valid model. Everything after the write is therefore verified
    against the artifact that will actually be released.
    """
    if record_directory is not None:
        verify_output_paths(
            (
                record_directory / "memote_before.json",
                record_directory / "memote_after.json",
                record_directory / "validation_summary.json",
            ),
            protected=baseline,
        )
    before = verify_source(baseline, manifest=manifest, expected=expected_sha256)
    baseline_verified_against = provenance_label(manifest, expected_sha256)
    base_model = load_model(baseline)
    result = check_candidate(base_model, load_model(candidate), request)
    if not result.ok:
        _write_validation_summary(
            record_directory,
            reaction_id=request.reaction_id,
            baseline_verified_against=baseline_verified_against,
            result=result,
            protected=baseline,
        )
        _refuse_unless_passed(result, "candidate")

    with staged_write(destination, protected=baseline) as staged:
        write_sbml_model(load_model(candidate), str(staged))

        # Reading the staged file back is the point: a writer that returns cleanly
        # having emitted unusable bytes would otherwise be published with a passing
        # verdict. load_model reports an unreadable file as `model_integrity`.
        published = load_model(staged)
        staged_result = check_candidate(base_model, published, request)
        if not staged_result.ok:
            _write_validation_summary(
                record_directory,
                reaction_id=request.reaction_id,
                baseline_verified_against=baseline_verified_against,
                result=staged_result,
                protected=baseline,
            )
            _refuse_unless_passed(staged_result, "staged deliverable")

        # Compare the semantic content of the candidate and staged deliverable so the
        # checks and artifacts describe the bytes that are about to be published.
        drift = diff_snapshots(
            semantic_snapshot(load_model(candidate)), semantic_snapshot(published)
        )
        if drift:
            if record_directory is not None:
                staged_result.record(
                    "staged deliverable matches candidate", ok=False, detail=str(drift)
                )
                _write_validation_summary(
                    record_directory,
                    reaction_id=request.reaction_id,
                    baseline_verified_against=baseline_verified_against,
                    result=staged_result,
                    protected=baseline,
                )
            msg = "staged deliverable differs from the candidate; nothing published"
            raise ValidationFailedError(msg, drift=drift, **staged_result.as_dict())

        verify_digest(baseline, before)
        digest = file_digest(staged)

        # Review the staged bytes, not the candidate before serialization.
        review = review_consistency(base_model, published)
        if record_directory is not None:
            write_json_artifact(
                record_directory / "memote_before.json",
                review.before.as_dict(),
                protected=baseline,
            )
            write_json_artifact(
                record_directory / "memote_after.json",
                review.after.as_dict(),
                protected=baseline,
            )
        regression = review.regression
        _write_validation_summary(
            record_directory,
            reaction_id=request.reaction_id,
            baseline_verified_against=baseline_verified_against,
            result=staged_result,
            regression=regression,
            protected=baseline,
        )
        if not regression.ok:
            msg = (
                "staged deliverable introduces a consistency regression MEMOTE did "
                "not report on the baseline; nothing published"
            )
            raise ValidationFailedError(
                msg,
                consistency_regression=regression.as_dict(),
                **staged_result.as_dict(),
            )

    return ExportResult(
        reaction_id=request.reaction_id,
        delivered=destination,
        delivered_sha256=digest,
        baseline_verified_against=baseline_verified_against,
        checks=staged_result.as_dict(),
        consistency_regression=regression.as_dict(),
    )


def _write_validation_summary(
    directory: Path | None,
    *,
    reaction_id: str,
    baseline_verified_against: str,
    result: CheckResult,
    protected: Path,
    regression: ConsistencyRegression | None = None,
) -> None:
    """Write the export verdict when recording was requested."""
    if directory is None:
        return
    payload = {
        "reaction_id": reaction_id,
        "baseline_verified_against": baseline_verified_against,
        **result.as_dict(),
    }
    if regression is not None:
        payload["scope"] = "export"
        payload["consistency_regression"] = regression.as_dict()
        if not regression.ok:
            payload["status"] = "failed"
            payload["failed"] = [*payload["failed"], "consistency regression"]
    write_json_artifact(
        directory / "validation_summary.json", payload, protected=protected
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
