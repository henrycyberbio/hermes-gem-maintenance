# Tool interface

Exact input and output shapes for `hermes-gem-maintenance`. Every example below is
real output from the worked example, not a sketch.

Fire accepts either `add_reaction` or `add-reaction`; the underscore form matches the
Python API.

## Reaction definition

The structured definition the agent produces and the package validates.

```json
{
  "reaction_id": "PKETF",
  "name": "Phosphoketolase (fructose-6-phosphate utilizing)",
  "metabolites": {"f6p_c": -1, "pi_c": -1, "actp_c": 1, "e4p_c": 1, "h2o_c": 1},
  "lower_bound": 0.0,
  "upper_bound": 1000.0,
  "gene_reaction_rule": "xfp",
  "subsystem": "Heterologous pathway"
}
```

Required: `reaction_id`, `metabolites`, `lower_bound`, `upper_bound`. Optional:
`name`, `subsystem`, `gene_reaction_rule`. Coefficients are signed — negative for
consumed, positive for produced — and must be non-zero. Any other key is ignored, so
provenance fields may be carried alongside for the human record.

## inspect

```bash
uv run hermes-gem-maintenance inspect --model=MODEL
```

```json
{
  "compartments": {"c": "cytosol", "e": "extracellular space", "p": "periplasm"},
  "genes": 1372,
  "metabolites": 1918,
  "model_id": "iEC1372_W3110",
  "objective": "1.0*BIOMASS_Ec_iJO1366_core_53p95M - 1.0*BIOMASS_..._reverse_5c8b1",
  "reactions": 2758
}
```

With `--reaction=ID` or `--metabolite=ID`, returns that object's structured fields.
An absent object is reported, not raised:

```json
{"absent": "PKETF", "kind": "reaction"}
```

Use this to confirm a target reaction does not already exist before proposing to add
it.

## resolve

```bash
uv run hermes-gem-maintenance resolve --model=MODEL --query="D-Fructose 6-phosphate"
```

```json
{
  "query": "D-Fructose 6-phosphate",
  "compartment": null,
  "count": 3,
  "unambiguous": false,
  "candidates": [
    {"id": "f6p_c", "name": "D-Fructose 6-phosphate", "compartment": "c",
     "formula": "C6H11O9P", "charge": 0, "matched_on": "exact name"},
    {"id": "f6p_e", "name": "D-Fructose 6-phosphate", "compartment": "e",
     "formula": "C6H11O9P", "charge": 0, "matched_on": "exact name"},
    {"id": "f6p_p", "name": "D-Fructose 6-phosphate", "compartment": "p",
     "formula": "C6H11O9P", "charge": 0, "matched_on": "exact name"}
  ]
}
```

Candidates are ordered strongest first by `matched_on`: `exact identifier`, then
`exact name`, then `identifier substring`, then `name substring`. The command never
picks a winner. `unambiguous` is true only when exactly one candidate matched.

Matching is literal, case-insensitive substring — there is no fuzzy matching and no
synonym table. Punctuation counts:

| Query | Result against this model |
| --- | --- |
| `fructose-6-phosphate` | 0 candidates — the model writes `D-Fructose 6-phosphate` |
| `Fructose 6-phosphate` | 1 candidate in compartment `c` |
| `phosphate` | 166 candidates |

`"count": 0` is a statement about the query, not about the model. Retry with a
shorter distinctive fragment, with the BiGG identifier, and without `--compartment`
before treating a metabolite as absent.

Three exact-name matches across compartments is the ambiguity case: ask which
compartment, do not rank them. Pass `--compartment=c` when the request settles it.

## add_reaction

```bash
uv run hermes-gem-maintenance add_reaction --model=MODEL --reaction=SPEC.json --output=CAND.xml
```

```json
{
  "baseline_sha256": "109290d2e2407a94f8088f9ef9fd40f6db6b73b1cf36574d6d987527ece8d9b7",
  "candidate": "candidate.xml",
  "candidate_sha256": "de83f0dae95778b7945f7d7c43eb483e61c8b87e5ab6dafdc68a8f8fdc602a38",
  "reaction_id": "PKETF"
}
```

Enforces only what it can decide while writing: the identifier must be new and every
metabolite must exist. **An unbalanced reaction is written without complaint.** The
digest is recomputed after the write to prove the baseline is untouched.

## check

```bash
uv run hermes-gem-maintenance check --model=MODEL --candidate=CAND.xml --reaction=SPEC.json
```

```json
{
  "reaction_id": "PKETF",
  "status": "passed",
  "failed": [],
  "unverifiable": [],
  "passed": [
    "reaction present: PKETF",
    "stoichiometry matches request: {'f6p_c': -1.0, 'pi_c': -1.0, 'actp_c': 1.0, 'e4p_c': 1.0, 'h2o_c': 1.0}",
    "bounds match request: (0.0, 1000.0)",
    "gene rule matches request: xfp",
    "mass and charge balance: balanced",
    "no unrelated semantic changes: only the requested reaction added"
  ]
}
```

A failing check exits 0 with `status: "failed"` — it is a verdict, not an error. The
`failed` entry carries the diagnosis:

```json
{"failed": ["mass and charge balance: {'H': -2.0, 'O': -1.0}"], "status": "failed"}
```

Those deltas are the correction: the reaction is short one water.

Entries under `unverifiable` did not fail — they could not be decided. A balance
check reports `missing formula/charge: <ids>` when a participant lacks the metadata.
Never present an unverifiable check as passed.

## export

```bash
uv run hermes-gem-maintenance export --model=MODEL --candidate=CAND.xml --reaction=SPEC.json --output=OUT.xml
```

Re-loads the candidate from disk, re-runs every check, and writes the deliverable
only if all pass. On success the payload carries `delivered`, `delivered_sha256`, and
the full check result. On failure nothing is written:

```json
{
  "category": "validation_failed",
  "message": "candidate failed re-validation; no deliverable written",
  "failed": ["reaction present: PKETF missing"],
  "passed": [],
  "status": "failed"
}
```

The re-check exists because serialization can normalize content. Do not skip export
on the grounds that `check` already passed.

## Error categories

Errors print to stderr as JSON and exit 1.

| Category | Example message | Context fields |
| --- | --- | --- |
| `insufficient_information` | `reaction definition is missing: lower_bound, upper_bound` | `missing`, or `query`/`candidates` for an ambiguous name |
| `request_violation` | `reaction ACKr already exists in the model` | `reaction_id`, `metabolites` |
| `validation_failed` | `candidate failed re-validation; no deliverable written` | `passed`, `failed`, `unverifiable`, `status` |
| `model_integrity` | `candidate path already exists` | `path`, or `expected`/`actual` for a digest mismatch |

Worked examples:

```json
{"category": "request_violation",
 "message": "metabolites absent from the model: nope_c",
 "metabolites": ["nope_c"], "reaction_id": "NEWRX"}
```

```json
{"category": "insufficient_information",
 "message": "reaction definition is missing: lower_bound, upper_bound",
 "missing": ["lower_bound", "upper_bound"]}
```

## Python API

The same operations, for tests and reuse. The CLI adds no logic of its own.

```python
from hermes_gem_maintenance import (
    ReactionRequest, add_reaction, check_candidate,
    load_model, save_candidate, file_digest, verify_digest,
    resolve_metabolite, require_unique_metabolite,
)
```

`require_unique_metabolite` raises `InsufficientInformationError` with the candidates
listed rather than returning a best guess — the same refusal the CLI reports.
