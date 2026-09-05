# Case selection: why this model and this reaction

Background for the first maintenance case. The operational instructions live in
[`examples/add-reaction/README.md`](../examples/add-reaction/README.md); this file
records why that case was chosen, so the reasoning is available without cluttering
the entry documentation.

## The chassis: iEC1372_W3110

Two published models were considered: BiGG `iEC1372_W3110` (*Escherichia coli* W3110)
and yeast-GEM v9.1.0 (*Saccharomyces cerevisiae*).

`iEC1372_W3110` was chosen. Verified against the frozen file with COBRApy, read-only:

- All five metabolites the reaction needs already exist, all in the cytosol with
  unambiguous identifiers: `f6p_c`, `pi_c`, `actp_c`, `e4p_c`, `h2o_c`.
- Every one carries both formula and charge, so `check_mass_balance()` returns a real
  verdict rather than "unverifiable". A first case that cannot exercise the
  conservation check would not prove the check works.
- The product `actp_c` is already consumed by `PTAr` and `ACKr`, so the new reaction
  joins the existing network instead of forming a dead end.
- Wild-type growth is 0.9824 h⁻¹, available as a baseline for detecting unintended
  change.

yeast-GEM v9.1.0 has no acetyl-phosphate metabolite at all. Introducing a
phosphoketolase there requires creating metabolites *and* adding several reactions at
once, which would make the first case exercise two kinds of operation
simultaneously. It remains a reasonable second case.

## The change: PKETF

*E. coli* has no native phosphoketolase. The enzyme is the core of the synthetic
non-oxidative glycolysis (NOG) pathway, which converts hexose to acetyl-CoA without
the carbon loss that pyruvate decarboxylation imposes (Bogorad *et al.*, 2013). This
makes it a genuine maintenance request with a published basis rather than a synthetic
exercise.

Identifier and stoichiometry follow the BiGG universal reaction record (King *et al.*,
2016), retrieved 2026-09-05 from <http://bigg.ucsd.edu/universal/reactions/PKETF>.

`PKETF`, `PKETX` and `PKL` were each confirmed absent from the base model, so the
request is genuinely an addition with no identifier conflict.

## Direction is a deliberate ambiguity

BiGG records the universal reaction as reversible. The request specifies the
irreversible forward direction, matching how the enzyme is used in engineered
strains, and states that choice in `reaction.json` under
`provenance.bound_rationale`.

This is intentional. A request that omits the bounds is genuinely ambiguous, and the
agent should ask rather than pick a direction. The case therefore exercises the
judgment boundary, not just the mechanical path.

## Scenarios this chassis also supplies

One frozen model covers every behaviour listed under TODO D:

| Scenario | Material |
| --- | --- |
| Second normal case | `PKETX`: `pi_c + xu5p__D_c <-> actp_c + g3p_c + h2o_c`, also absent and also balanced |
| Violation: identifier conflict | Submit an addition reusing `ACKr` |
| Violation: verifiable imbalance | Perturb a coefficient in either reaction |
| Insufficient information | Omit the bounds, or name a metabolite that does not resolve to one compartment |

## Gene rules must be compared as logic, not text

Found while building the walkthrough, and worth recording because it looks like data
corruption.

COBRApy removes redundant parentheses when writing SBML: `(A and B) and C` returns as
`A and B and C`. In this model that rewrites 27 reactions unrelated to the change.
Comparing gene rule strings reports all 27 as modifications; the first run of the
walkthrough failed its roundtrip check for exactly this reason.

Nothing was wrong with the model. The comparison was wrong. Gene rules are boolean
expressions, so the check parses both sides, flattens nested same-operator nodes and
sorts operands before comparing — 27 syntactic differences, 0 semantic. The same
principle already applied to the rest of the diff, which compares structured content
rather than SBML layout; the gene rule field had simply been left as a raw string.
