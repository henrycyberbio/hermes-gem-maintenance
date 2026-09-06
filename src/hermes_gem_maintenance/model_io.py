"""Reading baseline models and writing candidates to separate paths."""

from __future__ import annotations

import hashlib
import json
import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING

from cobra.io import read_sbml_model, write_sbml_model

from hermes_gem_maintenance.errors import ModelIntegrityError

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    import cobra

logger = logging.getLogger(__name__)

_DIGEST_CHUNK = 1 << 20


# ==== digests ====


def file_digest(path: Path) -> str:
    """SHA-256 of a file, read in chunks so a large SBML does not load into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_DIGEST_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def verify_digest(path: Path, expected: str) -> str:
    """Confirm a file matches a recorded digest and return it."""
    actual = file_digest(path)
    if actual != expected:
        msg = "baseline model does not match its recorded digest"
        raise ModelIntegrityError(msg, path=path.name, expected=expected, actual=actual)
    return actual


def approved_digest(manifest: Path) -> str:
    """The SHA-256 a source manifest records for its artifact."""
    try:
        record = json.loads(manifest.read_text(encoding="utf-8"))
        digest = record["artifact"]["sha256"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        msg = "source manifest does not record an artifact SHA-256"
        raise ModelIntegrityError(msg, path=manifest.name) from exc
    if not isinstance(digest, str) or not digest.strip():
        msg = "source manifest does not record an artifact SHA-256"
        raise ModelIntegrityError(msg, path=manifest.name)
    return digest


def verify_source(model: Path, *, manifest: Path | None, expected: str) -> str:
    """Confirm the model is the approved artifact, not merely unchanged just now.

    Comparing a file against a digest read from that same file moments earlier proves
    only that nothing changed during the command. It says nothing about drift that
    happened before the command started, which is the more common way a frozen input
    stops being the approved one. A caller that supplies a manifest or an expected
    digest gets the stronger guarantee; one that supplies neither is told, in the
    payload, which guarantee it actually received.
    """
    if manifest is not None:
        return verify_digest(model, approved_digest(manifest))
    if expected:
        return verify_digest(model, expected)
    return file_digest(model)


# ==== reading and writing ====


def load_model(path: Path) -> cobra.Model:
    """Read an SBML model. The file is only read; nothing is written back."""
    logger.info("reading model from %s", path.name)
    return read_sbml_model(str(path))


def save_candidate(model: cobra.Model, destination: Path, *, protected: Path) -> Path:
    """Write a candidate model, refusing to touch the protected baseline.

    The guard compares resolved paths, so a relative path, a different separator, or
    a symlink cannot slip past it. This protects the package's own writes only; it
    makes no claim about other tools with filesystem access.
    """
    destination = _guard_destination(destination, protected)
    destination.parent.mkdir(parents=True, exist_ok=True)
    logger.info("writing candidate to %s", destination.name)
    write_sbml_model(model, str(destination))
    return destination


@contextmanager
def staged_write(destination: Path, *, protected: Path) -> Iterator[Path]:
    """Yield a temporary path that becomes `destination` only on a clean exit.

    A deliverable is a claim that the run succeeded. Writing it before the last check
    has passed means a failure leaves a full-sized file sitting at the final path,
    indistinguishable from a real result. Staging beside the destination keeps the
    rename atomic (same filesystem) and guarantees the final path never exists unless
    every check that follows the write also passed.
    """
    destination = _guard_destination(destination, protected)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = destination.with_name(f".{destination.name}.partial")
    staged.unlink(missing_ok=True)
    try:
        yield staged
        staged.replace(destination)
        logger.info("published %s", destination.name)
    finally:
        staged.unlink(missing_ok=True)


def _guard_destination(destination: Path, protected: Path) -> Path:
    """Resolve an output path and refuse the baseline or an existing file."""
    destination = destination.resolve()
    if destination == protected.resolve():
        msg = "refusing to write over the baseline model"
        raise ModelIntegrityError(msg, path=destination.name)
    if destination.exists():
        msg = "candidate path already exists"
        raise ModelIntegrityError(msg, path=destination.name)
    return destination
