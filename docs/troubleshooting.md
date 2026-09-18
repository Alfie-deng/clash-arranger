# Troubleshooting

| Symptom | Check |
|---------|--------|
| `WEEKEND_IDLE` | Workstation `weekdays_only` + `no_op` skipped controller/probe/files |
| `PRE_PROBE_REJECT` | History looks fine; live `delay` failed both rounds |
| `CAS_ABORT` | Disk or API changed under the writer |
| `DOWNSTREAM_PENDING` | Primary wrote; downstream file did not. Not a full success. Retry with `primary_already_written` |
| `HEALTH_ESCAPE_EXHAUSTED` | Every escape set failed post-probe or write; config rolled back |
| `soft_fail_connection_drop` | Reload closed the socket. Success only if disk == API and post-probe passes |
| `config error` | Missing window, bad timezone, or secret file not `0600` |

Logs record filter reasons, mix degrade (`3+1`), four-state views, and the success definition.

Exit codes: `0` ok/dry-run/no-op, `2` usage, `3` probe reject, `4` downstream pending, `5` lock busy, `6` file committed / app unconfirmed, `7` rollback, `8` escape exhausted.
