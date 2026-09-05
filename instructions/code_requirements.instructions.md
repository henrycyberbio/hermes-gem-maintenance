# Code requirements

Applies to all Python in this repository.

## Quality

- Comments and docstrings in English, terse. Comment the *why*; code explains the what.
- No references to issues, tickets, chats, or agents in comments.
- Full type annotations on every parameter and return value, including `-> None`.
- All imports at module top.

## Style

- Double-quoted strings.
- Unimplemented method bodies are `pass`.
- Section blocks inside a module: `# ==== name ====`, one blank line between blocks.
- Match surrounding code when it disagrees with the above.

## Linter

Ruff, with this select set. Write code that passes without `noqa`:

```
I, E, W, N, UP, ANN, ASYNC, FBT, B, C4, DTZ, FA, LOG, T20, PT, RET, SIM, TID, TC,
PTH, ERA, TRY, FAST, PERF, FURB
```

Consequences worth stating: no `print()` (use logging, or return a value from a Fire
command); no `os.path` or bare `open()` (use `pathlib`); no naive datetimes; no
positional booleans in signatures; no commented-out code; no bare `except`; absolute
imports only; type-only imports under `if TYPE_CHECKING:`.

## Terminal modules

Shell-facing modules use Fire, not argparse. Docstrings become `--help` text, so a
missing docstring ships as a broken CLI. Return a str/dict/list rather than printing;
Fire serializes the return value. Name every parameter — `*args`/`**kwargs` leave
`--help` empty.

## Tests

Every rule above applies. Each test additionally carries a GIVEN / WHEN / THEN story
as inline comments stating why the case matters:

```python
def test_add_reaction_rejects_existing_identifier() -> None:
    # GIVEN a model that already contains ACKr.
    # WHEN adding a reaction that reuses that identifier.
    # THEN the call fails instead of silently overwriting the existing reaction.
```

Use `import pytest` rather than `from pytest import MonkeyPatch`.
