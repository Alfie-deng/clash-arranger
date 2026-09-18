# Architecture

Clash 节点编排器拆成采样、排位、混部、探活、事务五层。核心不引用任何 GUI 客户端名。

```text
sample
  -> rank
  -> filter
  -> mix
  -> pre-probe
  -> prepare
  -> write
  -> reload
  -> API readback
  -> post-probe
  -> downstream sync
  -> commit / rollback
```

## Two truths

1. **Score truth** — historical throughput, loss, TLS/target reachability, red ratio, sample count, confidence, inside a frozen `[start, end)` window.
2. **Liveness truth** — controller `delay` / `generate_204` right now. Delay is never speed.

The writer emits an ordered `fallback` list. Mihomo fails over when a node dies. The arranger only rewrites on sustained slowness, sustained death, config drift, or mass failure.

## Adapters

| Adapter | Role |
|---------|------|
| `mihomo-local` | Local controller API |
| `stash-file` | Downstream YAML file sync |
| `openclash-ssh` | Remote persistent-source file over injectable SSH |

## Success

`source/disk == runtime/API == downstream file AND post-apply probe passes`.

Phone-app hot reload cannot be observed. File transaction success is not app confirmation.
