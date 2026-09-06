# Records

Recorded CLI output for one successful request and the three scenarios in
`../scenarios/`. They are committed so the repository documents what the tool
actually returns, without requiring a reader to run a 10.8 MB model themselves.

Only the JSON records are kept. Candidate and delivered models are not committed —
they are ~11 MB each and reproducible from the inputs.

| File | What it records |
| --- | --- |
| `success-PKETF.json` | `add-reaction` and `check` for the worked example, all six checks passing |
| `insufficient-information-PGI2.json` | `resolve` returning three compartment candidates each for two metabolites, `resolved_id: null` |
| `duplicate-identifier-ACKr.json` | `inspect --reaction=ACKr` showing the identifier is already in the model |
| `unbalanced-PKETX.json` | The same reaction as written (fails, `{'H': -2.0, 'O': -1.0}`) and corrected (passes) |

Regenerate them with `uv run python scripts/record_examples.py write`. The success
record's candidate digest is stable at
`de83f0dae95778b7945f7d7c43eb483e61c8b87e5ab6dafdc68a8f8fdc602a38`; a change there
means the package's output changed.
