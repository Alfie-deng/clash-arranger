# Contributing

Thanks for helping keep seat changes boring and reversible.

## Ground rules

- Default every new command path to dry-run.
- Keep ranking, mixing, health, and YAML writes in separate modules.
- Do not add live subscription URLs, real node names, or private hosts.
- Do not call `yaml.dump()` on a full Mihomo/Clash document.
- Tests must use fixtures, tempdirs, or fake controllers. No live SSH.

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
ruff format
ruff check
mypy
pytest
python -m clash_arranger doctor --config examples/workstation.example.yaml
```

## Pull requests

Use the repository PR template. Every behavior change needs a regression test, especially around:

- fixed score windows
- banned-node fill
- two-round probes
- downstream failure
- weekend no-op
- reload disconnect branches

## Commits

Write short, imperative subjects. Do not attach generated caches or local `state/` files.
