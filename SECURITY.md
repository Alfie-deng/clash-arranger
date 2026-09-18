# Security Policy

## Supported versions

`v0.1.0` is an experimental first public release. Security fixes land on the default branch and are tagged when they ship. There is no long-term support promise yet.

## What this project will never do

- Read or write subscription URLs, controller secrets, SSH keys, or cookie jars from the repository tree.
- Rewrite a whole Mihomo/Clash YAML with `yaml.dump()`.
- Touch DNS, TUN, rules, or groups other than the configured target group.
- Treat a single controller `delay` sample as speed.

Secrets may come only from an environment variable, a system keychain item, or an external file whose mode is `0600`.

## Reporting a vulnerability

Please open a private GitHub security advisory on this repository, or email the address listed on the repository owner's public profile. Do not attach live configs, sample JSONL from a real network, or controller secrets.

Include:

1. Affected version or commit.
2. A minimal fictitious reproduction.
3. Impact (config overwrite, secret leakage, unsafe apply, etc.).

## Operator checklist

- Keep the default CLI in dry-run mode until you pass `--apply`.
- Human writes also need `--force-manual`.
- Point examples at `127.0.0.1:9090` and fictional node names only.
- Never commit `state/`, `logs/`, `.env`, or `*.bak_*` files.
