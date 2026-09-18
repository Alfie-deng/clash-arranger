# Configuration

Every path, controller URL, SSH host, group name, timezone, shift, seat count, provider rule, probe target, and threshold comes from YAML or environment variables.

## Required keys

See `examples/workstation.example.yaml` and `examples/router.example.yaml`.

| Key | Meaning |
|-----|---------|
| `profile` | `workstation` or `router` (free string; schedule decides behavior) |
| `timezone` | IANA zone, required |
| `windows.*` | `kind: today \| previous_day`, `start`, `end` (end exclusive) |
| `schedule.kind` | `weekdays_only` or `daily` |
| `mix.seat_count` | Seat count |
| `mix.providers` | Per-provider `min` / `max` |
| `mix.region_allowlist` | Allowed region codes |
| `health.*` | Probe URL, rounds, isolation, mass-failure |
| `promotion.*` | Paired-throughput thresholds |
| `controller.*` | Adapter, `127.0.0.1:9090` in examples, group, YAML path |
| `downstream.*` | Optional file sync |

## Secrets

Only:

- environment variable (`secret_env`)
- system keychain (`secret_keychain`)
- external file with mode `0600` (`secret_file`)

Examples never embed live secrets.

## Promotion defaults

| Key | Default |
|-----|---------|
| `min_samples` | 2 |
| `min_span_min` | 50 |
| `min_gain_mbps` | 8.0 |
| `min_ratio` | 1.35 |
| `two_sample_max_main_mbps` | 15.0 |
| `two_sample_min_gain_mbps` | 12.0 |
| `two_sample_min_ratio` | 1.75 |
| `cooldown_sec` | 3600 |
