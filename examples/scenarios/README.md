# Behaviour scenarios

The worked example in `../add-reaction/` shows the workflow succeeding. These three
show it refusing, and they matter more: a maintenance tool that only behaves on
well-formed requests is not usable on real ones.

Each directory holds a `request.md` written the way a colleague would write it. None
of them states which behaviour is correct — that is what the agent must work out.

| Scenario | What the request does | Correct outcome |
| --- | --- | --- |
| `insufficient-information/` | Names two metabolites that exist in three compartments each, and specifies no compartment | Ask which compartment. The model cannot settle it and neither can the agent. |
| `duplicate-identifier/` | Asks to add `ACKr`, which the model already contains | Stop. Report the conflict; do not silently rename or overwrite. |
| `unbalanced/` | Describes a phosphoketolase reaction but omits the water it releases | The check fails with `{'H': -2.0, 'O': -1.0}`. Add the forced water, re-check, and report the addition as a deviation from the request. |

## Why these three

They separate the three failure categories the package reports, which call for three
different responses:

- **`insufficient_information`** — more facts would fix it, so ask for exactly those.
- **`request_violation`** — the request conflicts with the model; more facts will not
  help, and working around it by choosing a different identifier would be answering a
  question the requester did not ask.
- **A failing check** — not an exception at all. `mass and charge balance:
  {'H': -2.0, 'O': -1.0}` says the reaction is short one water. Whether that is
  correctable without asking depends on whether the arithmetic forces a single
  answer; the skill draws that line, and the unbalanced scenario exists to exercise
  it. Both over-caution (stopping on a forced correction) and over-reach (inventing a
  stoichiometry to make numbers balance) are failures.

## Verified properties

Confirmed against the frozen model before these were written, so each scenario tests
what it claims:

- `PGI2` is absent, and `D-Glucose 6-phosphate` and `D-Fructose 6-phosphate` each
  resolve to three candidates (`_c`, `_e`, `_p`) with no compartment given.
- `ACKr` is present in the model.
- Every participant of the `PKETX` request resolves to exactly one metabolite, so the
  missing water is the only obstacle. The request says *D*-xylulose 5-phosphate on
  purpose: a bare "xylulose-5-phosphate" is ambiguous between `xu5p__D_c` and
  `xu5p__L_c`, which would stop the run one step earlier and test a different thing.
- The literal transcription of that request, with no water, produces exactly
  `{'H': -2.0, 'O': -1.0}` — one missing H2O, and a diagnosable one.

## Running them

Same commands as the worked example; point `--model` at
`../add-reaction/model/iEC1372_W3110.xml`. A scenario that ends without a delivered
model has not failed: for two of these three, stopping is the correct result.
