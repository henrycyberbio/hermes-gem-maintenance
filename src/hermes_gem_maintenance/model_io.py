"""Reading baseline models and writing candidates to separate paths."""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING

from cobra.io import read_sbml_model, write_sbml_model

from hermes_gem_maintenance.errors import ModelIntegrityError

if TYPE_CHECKING:
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
    destination = destination.resolve()
    if destination == protected.resolve():
        msg = "refusing to write over the baseline model"
        raise ModelIntegrityError(msg, path=destination.name)
    if destination.exists():
        msg = "candidate path already exists"
        raise ModelIntegrityError(msg, path=destination.name)

    destination.parent.mkdir(parents=True, exist_ok=True)
    logger.info("writing candidate to %s", destination.name)
    write_sbml_model(model, str(destination))
    return destination
