# Example: add a heterologous phosphoketolase reaction

Introduces one reaction into a published *Escherichia coli* model, using only
metabolites the model already defines.

## The change

| Field | Value |
| --- | --- |
| Reaction | `PKETF` |
| Name | Phosphoketolase (fructose-6-phosphate utilizing) |
| Stoichiometry | `f6p_c + pi_c -> actp_c + e4p_c + h2o_c` |
| Bounds | `0` to `1000` (irreversible forward) |
| Gene rule | `xfp` |

*E. coli* has no native phosphoketolase. The enzyme is the core of the synthetic
non-oxidative glycolysis pathway (Bogorad *et al.*, 2013); the identifier and
stoichiometry follow the BiGG universal record (King *et al.*, 2016), retrieved
2026-09-05 from <http://bigg.ucsd.edu/universal/reactions/PKETF>.

Why this model and this reaction: [`docs/case-selection.md`](../../docs/case-selection.md).
Full citations: [CITATIONS.md](../../CITATIONS.md).

## Files

| Path | Contents |
| --- | --- |
| `model/iEC1372_W3110.xml` | Frozen SBML input, never modified |
| `model/iEC1372_W3110.source.json` | Source, licence, retrieval record, byte count, SHA-256 |
| `reaction.json` | Structured reaction definition with provenance |

## Run

```bash
uv run python scripts/add_reaction_walkthrough.py run
```

Writes `candidate.xml` and `checks.json` to `runs/PKETF/`. The input is hashed before
and after; the candidate is written separately and read back to confirm the change
survives SBML.

All eight checks pass: reaction present, stoichiometry, bounds, gene rule, elemental
and charge conservation, no unrelated semantic changes, SBML roundtrip, input
unchanged.
