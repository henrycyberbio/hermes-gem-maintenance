---
name: gem-maintenance
description: "Use when asked to add or edit a reaction in a genome-scale metabolic model (GEM/SBML) — 'add this reaction to the model', 'introduce the phosphoketolase pathway into iML1515', 'update the GEM with this enzyme'. Drives the hermes-gem-maintenance CLI and decides when to ask instead of guess."
version: 0.1.0
license: MIT
---

# GEM maintenance

Turn a maintenance request in plain language into a verified change to a
genome-scale metabolic model.

The split of responsibility is the point of this skill. The `hermes-gem-maintenance`
package decides everything that follows from the model and the request: whether an
identifier exists, whether the stoichiometry conserves mass and charge, whether the
candidate changed anything it was not asked to. You decide what the package cannot:
what the request means, whether a mapping is unambiguous, and when to stop and ask.

Never invent biology to fill a gap. A metabolite, compartment, direction, or gene
rule that cannot be determined from the model plus the supplied evidence is a
question for the requester, not a value for you to choose.

## Commands

```bash
uv run hermes-gem-maintenance inspect  --model=MODEL [--reaction=ID | --metabolite=ID]
uv run hermes-gem-maintenance resolve  --model=MODEL --query=NAME [--compartment=C]
uv run hermes-gem-maintenance add_reaction --model=MODEL --reaction=SPEC.json --output=CAND.xml
uv run hermes-gem-maintenance check    --model=MODEL --candidate=CAND.xml --reaction=SPEC.json
uv run hermes-gem-maintenance export   --model=MODEL --candidate=CAND.xml --reaction=SPEC.json --output=OUT.xml
```

Every command prints JSON and exits non-zero on a deliberate error. Errors carry a
`category`; read that field rather than parsing the message.

Exact payload shapes, every error category with worked examples, and the Python API
are in `references/tool-interface.md`. Read it before the first call in a session.

## Working a request

`--model` takes a path to an SBML **file**, not a directory. When the request names a
folder, list it and pick the model file; a manifest such as `<model>.source.json`
sits beside it and is not itself a model.

Read the model before proposing anything. `inspect` gives counts and compartments;
`inspect --reaction=ID` confirms whether the target already exists. Resolve every
metabolite the request names with `resolve`, and read `matched_on` — an exact
identifier hit is not the same evidence as a name substring.

COBRApy writes progress and solver notes to stderr; stdout carries nothing but the
JSON payload. To consume a result, discard stderr — `... 2>/dev/null` — and parse
stdout directly. Use `2>&1 | grep -viE 'warning|optimality'` only when reading output
yourself, never when parsing: merging the streams puts log lines in front of the JSON
and breaks it.

Either way the pipeline reports grep's exit status rather than the command's, so a
failed call can look successful. Judge success by the payload — a `category` field
means it failed — or add `set -o pipefail`.

Build the structured definition yourself: reaction ID, stoichiometry keyed by
model-native metabolite IDs with signed coefficients, bounds, and the gene rule when
the request supplies one. Free text never reaches the writer.

Every field must trace to the request or to the model you just inspected. Worked
examples in the documentation show the *shape* of a payload, never the values for
your task — if you find yourself copying an identifier or a coefficient out of a
reference file, you have stopped deriving and started guessing.

Then `add_reaction` to a fresh path, `check` the result, and `export` only once the
checks pass. Write each run's request, definition, candidate, checks, and delivered
model to its own directory. When a run stops early, say where it stopped and why, and
do not leave a deliverable behind that implies success.

Recovery is always to regenerate a candidate from the untouched baseline. Never
repair a candidate in place — a candidate whose history you cannot reconstruct is not
evidence of anything.

## Reading a failure

The category tells you which of three different situations you are in, and they call
for three different responses:

| Category | What happened | What to do |
| --- | --- | --- |
| `insufficient_information` | A required field is absent, or a name matched several metabolites | Ask one specific question naming the candidates. Do not choose. |
| `request_violation` | Duplicate reaction ID, unknown metabolite, invalid bounds | The request conflicts with the model. Report the conflict with the evidence; more facts will not fix it. |
| `validation_failed` | A candidate did not pass its checks | The baseline is fine and the tool worked. Diagnose, then regenerate from the baseline. |
| `model_integrity` | Baseline digest mismatch, or a write would clobber a file | Stop. The inputs are not what they claim to be. |

A failing `check` is not an exception — it returns `status: "failed"` with the reason
in `failed`. Read the entry: a balance failure reports the element deltas, so
`{'H': -2.0, 'O': -1.0}` means the reaction is short exactly one water, which is a
concrete correction rather than a mystery.

Before reporting a failure to the requester, work out whether it was your mistake or
theirs. A coefficient you transcribed wrong, a compartment suffix you dropped, an
output path you reused — fix those yourself within the original request and re-check.
Only a genuine conflict or a genuine gap goes back to the user.

## Gotchas

- **`add_reaction` succeeding does not mean the reaction is valid.** It enforces only
  what it can decide while writing: no duplicate ID, no unknown metabolite. An
  unbalanced reaction is written without complaint and fails at `check`. Never report
  a successful add as a completed change.
- **Re-check from disk, not from memory.** A candidate that satisfies every check in
  memory can still differ after a write/read cycle, because serialization normalizes.
  `export` re-loads and re-checks for exactly this reason; do not skip it because
  `check` already passed.
- **Gene rules are compared as boolean logic, not text.** The writer drops redundant
  parentheses, so `(A and B) and C` comes back as `A and B and C`. That is not a
  change, and the package will not report it as one.
- **A balance verdict of "unverifiable" is not a pass.** When a participant lacks
  formula or charge, conservation cannot be checked. Say so; do not present the
  change as verified.
- **The output path must not exist.** Both `add_reaction` and `export` refuse an
  existing file rather than overwrite evidence. Use a new path per attempt.
- **Zero candidates usually means the query wording, not an absent metabolite.**
  Matching is literal substring, so punctuation and word order matter: the model
  writes `D-Fructose 6-phosphate`, and `fructose-6-phosphate` — the way a requester
  naturally types it — returns nothing at all. Some names are unusable outright:
  BiGG stores water as `H2O H2O`. An empty result is the most dangerous one, because
  it reads as "not in this model" and invites inventing the metabolite. Retry with a
  shorter fragment, then with the molecular formula (`H2O` finds `h2o_c`), then with
  the suspected identifier, and drop `--compartment`. Confirm absence with
  `inspect --metabolite=ID`.
- **A crowd of matches is not a resolution.** `resolve --query=phosphate` returns 166
  candidates and `resolved_id: null`, even though one is named exactly "Phosphate".
  A word that vague did not identify a metabolite. Narrow it: re-run `resolve` with
  the promising candidate's `id` and let the tool return a verdict, rather than
  lifting a row out of the list yourself.
- **Several exact matches in different compartments is ambiguity, not a ranking
  problem.** `resolve` returning `f6p_c` and `f6p_p` means the request did not say
  which compartment. Ask.
- **A reversible database entry is not a decision.** When a source records a reaction
  as reversible and the request implies one direction, surface the discrepancy
  instead of settling it silently.

## Verification

Before reporting success: `export` returned `status: "passed"`, the delivered file
exists at the path you name, the baseline digest is unchanged, and every claim you
make appears in the JSON you actually received. Report the checks that were
unverifiable alongside the ones that passed.
