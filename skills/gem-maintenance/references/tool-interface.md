# Tool interface

Exact input and output shapes for `hermes-gem-maintenance`. Every example below is
real output from the worked example, not a sketch.

Fire accepts either `add_reaction` or `add-reaction`; the underscore form matches the
Python API.

## Reaction definition

The structured definition the agent produces and the package validates. The example
below is deliberately **not** the repository's worked case: it restates `CITL`, a
reaction already present in `iEC1372_W3110`, so reading it cannot substitute for
deriving a definition from the model in front of you. (Submitting it as an addition
would be rejected as a duplicate identifier.)

```json
{
  "reaction_id": "CITL",
  "name": "Citrate lyase",
  "metabolites": {"cit_c": -1, "ac_c": 1, "oaa_c": 1},
  "lower_bound": 0.0,
  "upper_bound": 1000.0,
  "gene_reaction_rule": "Y7U_RS03200 and Y7U_RS03205",
  "subsystem": "Citric Acid Cycle"
}
```

Required: `reaction_id`, `metabolites`, `lower_bound`, `upper_bound`. Optional:
`name`, `subsystem`, `gene_reaction_rule`. Coefficients are signed — negative for
consumed, positive for produced — and must be non-zero. Any other key is ignored, so
provenance fields may be carried alongside for the human record.

Every value comes from the request or from the model, never from an example
elsewhere. Deriving the fields:

| Field | Where it comes from |
| --- | --- |
| `reaction_id` | The request, or the namespace record it cites. Confirm it is absent with `inspect --reaction=ID`. |
| `metabolites` | `resolve` each participant against the model. Use the returned `id`, never a name from the request. |
| Coefficients | The stoichiometry the request states. One molecule each unless it says otherwise. |
| `lower_bound` | `0` for an irreversible forward reaction, `-1000` when reversible. |
| `upper_bound` | `1000`, the convention this model uses for an unconstrained bound — read a few existing reactions with `inspect --reaction=ID` and match them rather than assuming. |
| `gene_reaction_rule` | The request. Absent unless supplied; do not invent a gene. |
| `name`, `subsystem` | The request, or omit. They are optional and affect nothing. |

When the request fixes a direction but names no numeric bound, take the magnitude
from the model's own convention and say in your report that you did so. When the
model shows no consistent convention, ask.

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
{"absent": "SOMERXN", "kind": "reaction"}
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
  "resolved_id": null,
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
`exact name`, then `exact formula`, then `identifier substring`, then `name
substring`. The command never picks a winner by ranking, but it does report a verdict:
`resolved_id` names the single candidate a caller may act on, or is `null`.

A query is settled when exactly one *exact* match came back and only a handful of
weak ones sit beside it — `pi_c` matches one identifier exactly and three as
substrings, and calling that ambiguous would block every short identifier. It is
unsettled in two cases: two exact matches compete (the same name in two
compartments), or an exact hit is buried in a crowd of weak matches. `phosphate`
names one metabolite exactly and appears in 165 others; a word that vague identified
nothing, so `resolved_id` is `null` even though an exact-name candidate is in the
list.

**Refining a crowded result is legitimate.** When a broad query returns `null` but
its candidate list shows a plausible exact-name hit, re-run `resolve` with that
candidate's `id`. The narrow query either settles or does not, and the verdict comes
from the tool rather than from you picking a row. Reading an identifier out of a
candidate list and using it *without* re-resolving is guessing.

Matching is literal substring, and case handling differs by field because the fields
are different kinds of data. Identifiers and formulae are compared **case-sensitively**
— `CO` is carbon monoxide and `Co` is cobalt, so folding them together and calling the
result an exact match is a chemistry error. Names are case-insensitive. Punctuation
counts, and BiGG names are often not the words a requester uses:

| Query | Compartment | Result against this model |
| --- | --- | --- |
| `fructose-6-phosphate` | any | 0 candidates — the model writes `D-Fructose 6-phosphate` |
| `Fructose 6-phosphate` | `c` | 1 candidate, `resolved_id: f6p_c` |
| `Fructose 6-phosphate` | none | 3 candidates, `resolved_id: null` — one per compartment |
| `water` | any | 0 candidates — BiGG names water `H2O H2O` |
| `H2O` | `c` | 3 candidates, `resolved_id: h2o_c` via `exact formula` |
| `H2O` | none | 7 candidates, `resolved_id: null` — water exists in all three |
| `phosphate` | `c` | 166 candidates, `resolved_id: null` — too vague to settle |
| `pi_c` | none | 4 candidates, `resolved_id: pi_c` — one exact id, rest substrings |

The compartment column is not decoration. The same query resolves or refuses
depending on it, because a species present in three compartments is three
metabolites. A figure quoted without its compartment is not reproducible.

**Formula is the escape hatch for an unusable name.** When a metabolite's name cannot
be guessed, query its molecular formula: an exact formula match outranks substring
kinds and is how `h2o_c` is reachable at all. It settles the query only when one
metabolite in scope carries that formula, so `C6H11O9P` returns `null`.

`"count": 0` is a statement about the query, not about the model. Retry with a
shorter distinctive fragment, with the formula, with the suspected identifier, and
without `--compartment` before treating a metabolite as absent.

Three exact-name matches across compartments is the ambiguity case: ask which
compartment, do not rank them. Pass `--compartment=c` when the request settles it.

## add_reaction

```bash
uv run hermes-gem-maintenance add_reaction --model=MODEL --reaction=SPEC.json --output=CAND.xml \
  --source_manifest=MODEL.source.json
```

```json
{
  "baseline_sha256": "109290d2e2407a94f8088f9ef9fd40f6db6b73b1cf36574d6d987527ece8d9b7",
  "baseline_verified_against": "source manifest: iEC1372_W3110.source.json",
  "candidate": "c.xml",
  "candidate_sha256": "8083404ad9f555300e44379aead00744b48715d4f24f8a876226bde923ac1f34",
  "reaction_id": "DEMO_ATPH"
}
```

Enforces only what it can decide while writing: the identifier must be new, every
metabolite must exist, and the gene rule must parse. **An unbalanced reaction is
written without complaint** — that is `check`'s job.

**Pass `--source_manifest` whenever a manifest exists.** Without it the command
digests the file it was handed and compares it to itself, which proves only that
nothing changed during those few seconds. It cannot detect a baseline that had
already drifted before the command started, which is the ordinary way a frozen input
stops being the approved one. `baseline_verified_against` says which guarantee you
actually got; `self-digest only` in that field means provenance was never checked.
`--expected_sha256=<digest>` does the same job when there is no manifest file.

The candidate is written to a staged path and moved into place only after the
baseline is re-verified, so a failed run leaves nothing at `--output`.

## check

```bash
uv run hermes-gem-maintenance check --model=MODEL --candidate=CAND.xml --reaction=SPEC.json
```

```json
{
  "reaction_id": "DEMO_ATPH",
  "status": "passed",
  "failed": [],
  "unverifiable": [],
  "passed": [
    "reaction absent from baseline: DEMO_ATPH",
    "reaction present: DEMO_ATPH",
    "stoichiometry matches request: {'atp_c': -1.0, 'h2o_c': -1.0, 'adp_c': 1.0, 'pi_c': 1.0, 'h_c': 1.0}",
    "bounds match request: (0.0, 1000.0)",
    "gene rule matches request: demoGene",
    "name matches request: Demonstration ATP hydrolysis",
    "subsystem matches request: (none)",
    "mass and charge balance: balanced",
    "diff is exactly the requested addition: added: ['DEMO_ATPH']"
  ]
}
```

`status` has three values, not two: `passed`, `failed`, and `unverifiable`. Every
field the request specifies is compared, and so is every field it *omits* — a
candidate carrying a name, subsystem or gene rule the request never asked for fails,
because inventing metadata is the silent edit these checks exist to catch. `(none)`
in a detail line means the request left that field empty and the candidate agreed.

The first and last entries are the shape of the operation, not decoration. `check`
and `export` are independent commands: neither may assume `add_reaction` ran first
and refused a duplicate, so the reaction must be *absent from the baseline* and the
diff must be *exactly one addition*. A candidate that merely contains the reaction —
including a byte-copy of a baseline that already had it — is not an addition.

A failing check exits 0 with `status: "failed"` — it is a verdict, not an error. The
`failed` entry carries the diagnosis:

```json
{"failed": ["mass and charge balance: {'H': -2.0, 'O': -1.0}"], "status": "failed"}
```

Those deltas name the missing species and its side: the reaction is short one water,
and the negative sign means it belongs among the products (`h2o_c: 1`). A delta is
not a licence to add whatever balances the numbers — SKILL.md sets out when a
correction may be made without asking.

Entries under `unverifiable` did not fail — they could not be decided. A balance
check reports `missing formula/charge: <ids>` when a participant lacks the metadata.
That produces `status: "unverifiable"`, a third state alongside `passed` and
`failed`, and `export` refuses to deliver it. Never present an unverifiable check as
passed.

## export

```bash
uv run hermes-gem-maintenance export --model=MODEL --candidate=CAND.xml --reaction=SPEC.json --output=OUT.xml
```

Re-loads the candidate from disk, re-runs every check, **writes the deliverable to a
private staged path, loads that file back and checks it again**, and only then
publishes it by no-clobber rename. A candidate with nothing in `failed` but something
in `unverifiable` is refused too, with a distinct message — an untested model must not
ship as a verified one. The checks reported in the payload describe the staged
artifact that was actually released, not the in-memory object it came from: existing
on disk and having a SHA-256 is not evidence of being a valid model. On success the
payload carries `delivered`, `delivered_sha256`, and the full check result. On failure
nothing is written and no partial file is left behind:

```json
{
  "category": "validation_failed",
  "message": "candidate failed re-validation; no deliverable written",
  "failed": ["reaction present: DEMO_ATPH missing"],
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
| `request_violation` | `reaction <ID> already exists in the model` | `reaction_id`, `metabolites` |
| `validation_failed` | `candidate failed re-validation; no deliverable written` | `passed`, `failed`, `unverifiable`, `status` |
| `validation_failed` | `candidate could not be fully verified; no deliverable written` | as above, with `status: "unverifiable"` |
| `model_integrity` | `candidate path already exists` | `path`, or `expected`/`actual` for a digest mismatch |

The two `validation_failed` messages call for different responses. "Failed
re-validation" means a check decided against the candidate: fix the definition and
regenerate. "Could not be fully verified" means a check could not run at all —
usually a participant missing formula or charge — so regenerating the same candidate
changes nothing; report what could not be checked and why.

A malformed definition is a `request_violation`, not a crash: `metabolites` given as
a list, a non-numeric bound, an infinite coefficient and a non-string name all arrive
as structured JSON with a category.

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

The same operations, for tests and reuse. The CLI adds no logic of its own: it parses
arguments, calls these functions, and serializes the result.

```python
from hermes_gem_maintenance import (
    ReactionRequest, add_reaction, check_candidate,
    build_candidate, publish_deliverable,
    load_model, file_digest, verify_digest, verify_source,
    resolve_metabolite, require_unique_metabolite,
)
```

`build_candidate` and `publish_deliverable` are what the CLI's `add_reaction` and
`export` call. Use them rather than assembling `save_candidate` yourself: the source
verification, staged write, re-check of the published bytes and no-clobber publication
live inside them, so hand-rolling the sequence produces a weaker artifact that looks
the same.

`require_unique_metabolite` raises `InsufficientInformationError` with the candidates
listed rather than returning a best guess — the same refusal the CLI reports.
