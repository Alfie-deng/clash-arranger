# Deployment

This project does not deploy itself onto anyone's live Clash / OpenClash / Stash / Tailscale setup.

## Workstation

- Run the CLI next to a local Mihomo-compatible client.
- Example LaunchAgent: `examples/launchd/com.example.clash-arranger-morning.plist`
- Keep weekday `no_op` if you do not want weekend controller access.
- First runs must stay dry-run (no `--apply`).

## Router

- Persistent OpenClash/Mihomo source of truth on the router.
- Scheduler on an always-on Linux host (`examples/systemd/`, `examples/cron/router.cron`).
- Morning and evening shifts plus an all-day health watch.
- Runs every calendar day.

## OpenClash source of truth

Write the persistent config path, then let OpenClash generate runtime. Do not edit only the runtime file.

## Downstream YAML

`stash-file` patches one group and may inject missing proxy definitions from a source document. It never `yaml.dump()`s the whole file. App hot-load is unconfirmed.
