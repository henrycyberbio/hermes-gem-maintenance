"""Regenerate the committed CLI records under ``examples/records``.

The records document what the package actually returns for the worked example and for
the three scenarios in ``examples/scenarios``. Committing them keeps the repository
honest about tool behaviour without asking a reader to run a 10.8 MB model, and
regenerating them after a change shows whether that behaviour moved.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import fire

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
MODEL = REPO_ROOT / "examples" / "add-reaction" / "model" / "iEC1372_W3110.xml"
REACTION = REPO_ROOT / "examples" / "add-reaction" / "reaction.json"
RECORDS = REPO_ROOT / "examples" / "records"

PKETX_AS_WRITTEN: dict[str, Any] = {
    "reaction_id": "PKETX",
    "metabolites": {"xu5p__D_c": -1, "pi_c": -1, "actp_c": 1, "g3p_c": 1},
    "lower_bound": 0.0,
    "upper_bound": 1000.0,
    "gene_reaction_rule": "xfp",
}


def _cli(*args: str) -> dict[str, Any]:
    """Run one CLI subcommand and return its parsed JSON payload.

    stderr is discarded: the package writes its payload to stdout and COBRApy writes
    progress notes to stderr, so merging them would corrupt the JSON.
    """
    completed = subprocess.run(
        [sys.executable, "-m", "hermes_gem_maintenance.cli", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=True,
    )
    return json.loads(completed.stdout)


def _write(name: str, payload: dict[str, Any]) -> None:
    path = RECORDS / name
    text = json.dumps(payload, indent=2) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    logger.info("wrote %s", path.relative_to(REPO_ROOT))


def write() -> None:
    """Regenerate every record file from the frozen model."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    RECORDS.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp)

        candidate = scratch / "candidate.xml"
        success = {
            "add": _cli(
                "add_reaction",
                f"--model={MODEL}",
                f"--reaction={REACTION}",
                f"--output={candidate}",
            ),
            "check": _cli(
                "check",
                f"--model={MODEL}",
                f"--candidate={candidate}",
                f"--reaction={REACTION}",
            ),
        }
        _write("success-PKETF.json", success)

        ambiguous = {
            query: _cli("resolve", f"--model={MODEL}", f"--query={query}")
            for query in ("D-Glucose 6-phosphate", "D-Fructose 6-phosphate")
        }
        _write("insufficient-information-PGI2.json", ambiguous)

        _write(
            "duplicate-identifier-ACKr.json",
            _cli("inspect", f"--model={MODEL}", "--reaction=ACKr"),
        )

        corrected = json.loads(json.dumps(PKETX_AS_WRITTEN))
        corrected["metabolites"]["h2o_c"] = 1
        unbalanced: dict[str, Any] = {}
        pairs = (("as_written", PKETX_AS_WRITTEN), ("corrected", corrected))
        for tag, definition in pairs:
            spec = scratch / f"{tag}.json"
            spec.write_text(json.dumps(definition), encoding="utf-8")
            model_out = scratch / f"{tag}.xml"
            _cli(
                "add_reaction",
                f"--model={MODEL}",
                f"--reaction={spec}",
                f"--output={model_out}",
            )
            unbalanced[tag] = {
                "definition": definition,
                "check": _cli(
                    "check",
                    f"--model={MODEL}",
                    f"--candidate={model_out}",
                    f"--reaction={spec}",
                ),
            }
        _write("unbalanced-PKETX.json", unbalanced)


if __name__ == "__main__":
    fire.Fire({"write": write})
