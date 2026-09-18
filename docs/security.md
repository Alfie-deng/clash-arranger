# Security notes

- Default command path is dry-run.
- Live writes need `--apply`. Human writes also need `--force-manual`.
- Scheduler writes and human writes are separate.
- Only the target proxy group is patched. DNS, TUN, rules, and other groups stay byte-identical aside from optional missing proxy-definition inject.
- Snapshots are taken before write. Failed verify restores the exact previous text.
- Do not commit `state/`, `.env`, `*.bak_*`, or live JSONL.
- Report vulnerabilities via a GitHub security advisory. See [SECURITY.md](../SECURITY.md).
