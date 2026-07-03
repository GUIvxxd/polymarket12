# Polymarket Paper Bot

This repository is for a Polymarket paper-trading research bot. The first goal is to test
crypto up/down market signals with realistic simulated fills, a local ledger, and repeatable
analysis before considering any other workflow.

## Safety Rules

- Paper trading only.
- Do not implement real Polymarket order placement.
- Do not ask for private keys, seed phrases, API secrets, or wallet credentials.
- Do not call authenticated trading endpoints.
- No signing or live execution.
- All simulated trades must be saved to a local ledger.

## Current Phase

Phase 1 is project setup. The package exposes a placeholder CLI and tests that prevent
accidental live-trading primitives from entering the source tree.

## Setup

```bash
uv sync
uv run pytest -q
uv run python -m polybot.main --help
```

If `uv` is not installed yet, use an available Python 3.11+ interpreter for local checks:

```bash
py -m pytest -q
$env:PYTHONPATH = "src"; py -m polybot.main --help
```

