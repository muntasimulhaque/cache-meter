# Cache Meter for Hermes

pi-style prompt-cache stats in the **Hermes desktop status bar** — inspired by the
[pi coding agent](https://github.com/badlogic/pi-mono) footer:

```
↑87k ↓36k R12.3M CH 97.9% $0.42
 ↑in  ↓out  R read   W write   CH cache-hit %   $ est. cost
```

- **↑ / ↓** uncached input / output tokens (cumulative, this session)
- **R** tokens served from the prompt cache · **W** tokens written to it
- **CH** cache-hit ratio = `R / (input + R + W)` — exactly pi's formula
- **$** estimated session cost (actual cost when the provider reports one)

Hover for the full breakdown: prompt volume, cached/uncached split, context-window
fill, API call count.

## Why this works with zero config

Hermes already tracks `cache_read_tokens` / `cache_write_tokens` / cost per session
in its own database (`state.db`). This plugin ships two halves:

| Half | What it does |
|---|---|
| Python backend (`dashboard/plugin_api.py`) | Reads your local `state.db` and exposes `/api/plugins/cache-meter/usage/<id>` + `/summary` inside the Hermes process |
| Desktop chip (`desktop/plugin.js`) | Status-bar chip that polls that endpoint every 5s |

No core patches, no provider changes. If a future/patched Hermes also exposes cache
fields via the `session.usage` gateway RPC, the chip prefers those live numbers
automatically.

> Note: numbers reflect what's persisted so far, so mid-turn they can lag the last
> API response by a few seconds (Hermes batches DB writes). The ratio converges by
> turn end.

## Install

Requirements: [Hermes Agent](https://github.com/NousResearch/hermes-agent) with the desktop app, any OS.

### One click (recommended)

**[Install in Hermes](hermes://plugin/install?repo=muntasimulhaque/cache-meter&enable=1)**

The link opens the Hermes desktop app with an install dialog that pre-checks both halves
(Agent plugin + Desktop status-bar chip). Confirm once and the chip appears in the status
bar: no toggles, no restart, no terminal.

Notes:

- If your browser does not open the link, use the CLI route below.
- Settings → Plugins will list two Cache Meter entries after this install (the standalone
  chip copy and the package's own desktop half). That is expected; the chip that renders
  is the one that shows ON.

### CLI

```bash
hermes plugins install muntasimulhaque/cache-meter
hermes plugins enable cache-meter
```

Important: this enables the agent half (the stats backend) only. The desktop chip is a
separate, opt-in half by Hermes' security model, so one more toggle is needed:

1. Open the desktop app → Settings → Plugins.
2. Under **Desktop plugins**, switch **Cache Meter** on.

The chip appears immediately (no restart needed).

Prefer manual? Clone `muntasimulhaque/cache-meter` with any Git client, copy the folder
into `$HERMES_HOME/plugins/`, and run `hermes plugins enable cache-meter`, then flip the
desktop toggle as above.

`$HERMES_HOME` is `~/.hermes` by default (`%LOCALAPPDATA%\hermes` on Windows).

## Updating

There is **no auto-update** — your install stays at whatever revision you pulled,
and new features land only when you ask for them:

```bash
hermes plugins update cache-meter
```

What that does (Hermes built-in behaviour):

1. `git pull`s the latest `main` from this repo into your installed copy.
2. Re-runs the security scan — if an update is flagged dangerous, the plugin is
   **disabled automatically** instead of staying active.
3. Records the new revision in the install metadata.

After updating:

- **Stats backend (Python)**: `hermes plugins update cache-meter` handles it;
  the backend mounts once at gateway startup, so restart once (`hermes gateway
  restart`, or `/restart` inside Hermes) for code changes to take effect.
- **Chip (JS)**: the chip's standalone copy under `$HERMES_HOME/desktop-plugins/`
  is not touched by `hermes plugins update`. Re-run the
  [install link](hermes://plugin/install?repo=muntasimulhaque/cache-meter&enable=1),
  check **Force reinstall**, and **untick Agent plugin** (tick Desktop UI only),
  then Install; the chip hot-reloads within seconds. (Keeping Agent ticked
  force-reinstalls a folder the running gateway may hold open: on Windows that
  fails with Access denied.)

To freeze a specific version instead of tracking `main`, install pinned to a
commit; updates then refuse until you move it:

```bash
hermes plugins install muntasimulhaque/cache-meter --force --ref <40-char-commit-sha>
```

## Verify it works

Send the agent a couple of messages, then look at the status-bar chip. Real example
from this machine (mid-session):

```
↑267k ↓79k R12.7M CH 97.9%
```

CLI check without the UI (GET the summary endpoint; replace the port with your
gateway's local port):

```
GET localhost:<gateway-port>/api/plugins/cache-meter/summary
```

## Uninstall

```bash
hermes plugins remove cache-meter
```

One-click installs also leave the chip's standalone copy: delete
`$HERMES_HOME/desktop-plugins/cache-meter/` (Settings → Plugins → Desktop
plugins → Cache Meter → folder icon reveals it), or the chip keeps rendering
with a dead backend.

## How the ratio is computed

Providers report prompt-cache hits differently (Anthropic top-level fields, OpenAI
`prompt_tokens_details.cached_tokens`, DeepSeek native hit/miss…). Hermes normalizes
all of them into `cache_read_tokens` + `cache_write_tokens`, with `input_tokens`
excluding cached tokens. That matches pi's convention, so:

```
prompt volume = input + cacheRead + cacheWrite
CH%           = cacheRead / prompt volume × 100
```

Providers that don't report cache activity show no CH segment rather than a fake 0%.

## License

MIT
