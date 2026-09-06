"""Error taxonomy for GEM maintenance operations.

The distinction matters to the caller: an agent must react differently to a request
it cannot complete without more facts, a request that conflicts with the model, and
a tool that broke. Collapsing them into one exception type forces the caller to
parse messages.
"""

from __future__ import annotations


class GemMaintenanceError(Exception):
    """Base class for every error this package raises deliberately."""

    category = "error"

    def __init__(self, message: str, **context: object) -> None:
        super().__init__(message)
        self.message = message
        self.context = context

    def as_dict(self) -> dict[str, object]:
        """Structured form for JSON output."""
        return {
            "category": self.category,
            "message": self.message,
            **self.context,
        }


class InsufficientInformationError(GemMaintenanceError):
    """The request cannot be resolved without facts the caller must supply.

    Raised when a name maps to several candidates, or a required field is absent.
    The caller should ask a specific question rather than choose.
    """

    category = "insufficient_information"


class RequestViolationError(GemMaintenanceError):
    """The request conflicts with the model or with a rule the package enforces.

    Raised for a duplicate identifier, an unknown metabolite, or a definition that
    fails a deterministic check. More information will not help; the request is wrong.
    """

    category = "request_violation"


class ModelIntegrityError(GemMaintenanceError):
    """A baseline artifact fails its digest check, or a write would clobber it."""

    category = "model_integrity"


class ValidationFailedError(GemMaintenanceError):
    """A candidate did not pass its checks, so nothing was delivered.

    Distinct from ModelIntegrityError on purpose: the baseline is fine and the tool
    worked. Recovery is to regenerate the candidate from the untouched baseline, not
    to abandon the run, so the caller must be able to tell the two apart without
    reading the message.
    """

    category = "validation_failed"
