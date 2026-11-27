# Frametrace dataset

Benchmark captures and comparisons shared by people who use
[Frametrace](https://github.com/melle62/frametrace), the game benchmarking tool that turns a
capture into a prompt telling an AI which in-game settings to change for a goal (higher average
FPS, higher 1% lows, lower latency, less stutter or a target FPS).

Everything people share is kept here, noisy runs included. The Frametrace repository's
`tools/aggregate.py` reads all of it, judges every capture, comparison and contributor, and
puts only the trustworthy part (one-change comparisons by contributors who benchmark
repeatably) into the game skills' **Community data** tables.

## Where the data is

- **Branch [`data`](../../tree/data)**: one JSON file per submission, plus `stats.json`. The
  branch is rewritten by every export as a single commit, so it has no history: data someone
  deleted is gone from it, not just hidden in an old commit.
- **Releases `csv-YYYY-MM`**: each capture's PresentMon CSV, gzipped, with the game's own rows
  only. A capture's JSON names its CSV (`csv.url`, with size and SHA-256).

```
data branch
├─ captures/<game>/<contributor>/<capture id>.json
├─ comparisons/<game>/<contributor>/<capture A>~<capture B>.json
├─ stats.json   counts per kind and game, the newest submission's time, and per game
│               (by_game) benchmarks, bundles, labels, settings read and builds
└─ README.md
```

`<game>` is the executable's name (`cs2` for cs2.exe). `<contributor>` is a 16-character key
derived from a random ID the app created for that installation, with a salt that only the
inbox knows: it groups one person's runs without saying who they are.

Get it with `git clone --depth 1 --branch data https://github.com/melle62/frametrace-data`.

## A record

```json
{
  "kind": "capture",
  "contributor": "a1b2c3d4e5f60718",
  "local_id": "20261126-141012-cs2",
  "schema": 1,
  "app_version": "0.0.1",
  "received_at": "...", "updated_at": "...",
  "csv": {"url": "https://github.com/melle62/frametrace-data/releases/download/...", "bytes": 0, "sha256": "..."},
  "data": { "capture": {}, "goal": {}, "game": {}, "game_settings": {}, "analysis": {}, "system": {}, "checks": [], "user": {} }
}
```

A capture's `data` is Frametrace's `result.json` as described in the Frametrace repository's
[`docs/data.md`](https://github.com/melle62/frametrace/blob/main/docs/data.md): frame-time
statistics, the bottleneck split, display path, PC latency, the game (name, store build,
engine), its in-game settings when Frametrace could read them, the hardware and the Windows
settings that matter for games, plus what the user added in `data.user`:

| Field | What it is |
|---|---|
| `label` | the user's name for the run ("Shadows medium") |
| `group` | a group the user moved the run into |
| `benchmark` | the user's tag for the scene ("Mirage Benchmark"): runs of another tag are another scene |
| `bundle` | the bundle the user put the run in: runs with the same `bundle` are repeats of one setup, combined in the window (a run that disagrees beyond noise is left out) |
| `advice` | who suggested the changes since the run before ("Claude", "A guide or video", ...) |
| `split` | left over from before bundles were manual; ignore it |

A comparison's `data` is what the Compare view showed: both capture IDs, the goal, the
verdict, every metric's change, the settings that changed and each side's benchmark. A side
that is a bundle is its runs combined: `a` / `b` is then its newest run that counts and
`a_runs` / `b_runs` lists every run that counts (`null` for a single run); such comparisons
have `bundle.<bundle id>` for that side in their file name. Comparisons shared before
2026-11-28 always compare single runs, even when the user compared bundles.

## Privacy

Sharing is opt-in in the Frametrace window and can be switched off at any time. Bundles never
contain names, account IDs, e-mail addresses, file paths with a user name, the window title or
the other apps that were running. "Delete my shared data" in the window deletes everything
from the inbox at once and from this repository at the next export (the `data` branch and the
CSV assets). Copies someone downloaded before that can't be recalled. Full notice:
[PRIVACY.md](https://github.com/melle62/frametrace/blob/main/PRIVACY.md).

## How it's updated

`.github/workflows/export.yml` runs `tools/export.py` daily at 02:23 UTC and again at
04:17 UTC (GitHub can start scheduled runs hours late; a run with nothing new pushes nothing).
The time of the last run is on the repository's Actions page and in the public API:
`https://api.github.com/repos/melle62/frametrace-data/actions/workflows/export.yml/runs?per_page=1`. It authenticates to the inbox
with a GitHub Actions OIDC token issued to this repository's main branch (the inbox checks
the token's repository and branch; there is no stored secret), writes the new submissions,
moves the CSVs into the month's release, applies deletions, pushes `data` and then tells the
inbox what was published.

## License

MIT, see [LICENSE](LICENSE). Contributors agree to that when they turn sharing on.
