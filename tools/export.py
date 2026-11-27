"""Publish new Frametrace submissions on this repository's `data` branch (runs in GitHub Actions).

1. Ask GitHub for an OIDC token (audience "frametrace-export"); the inbox's export function
   accepts only tokens issued to this repository's main branch, so no secret is stored.
2. Pull every submission not exported yet, page by page.
3. Write each one as JSON under captures/ or comparisons/, and move each capture's CSV
   (the game's rows only, gzipped) into a monthly release, `csv-YYYY-MM`.
4. Remove every contributor on the deletions list: their JSON files and their CSV assets.
5. Commit the whole tree as a single new root commit and force-push it to `data`. The branch
   never has history, so deleted data is gone from it, not just hidden in an old commit.
6. Acknowledge the rows, so the inbox marks them exported and drops the archived CSVs.

Stdlib only, plus git and the GitHub CLI (both on the runner).

    python tools/export.py            # in the workflow
    python tools/export.py --dry-run  # pull and write locally, no release, push or ack
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

EXPORT_URL = os.environ.get(
    "EXPORT_URL", "https://hompvqrkzyoeljcrastw.supabase.co/functions/v1/export"
)
REPO = os.environ.get("GITHUB_REPOSITORY", "melle62/frametrace-data")
WORK = Path(os.environ.get("DATA_DIR", "dataset"))
BRANCH = "data"
PAGE = 200
MAX_PAGES = 50  # 10,000 rows per run; the rest goes on the next run
ASSETS_PER_RELEASE = 900  # GitHub allows 1,000 assets per release


def log(msg: str) -> None:
    print(msg, flush=True)


def run(*cmd: str, check=True, capture=True, env=None) -> str:
    out = subprocess.run(cmd, check=check, capture_output=capture, text=True, env=env)
    return (out.stdout or "").strip()


# --- the inbox -------------------------------------------------------------------------------


def oidc_token() -> str:
    url = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"] + "&audience=frametrace-export"
    req = urllib.request.Request(
        url, headers={"Authorization": "bearer " + os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)["value"]


def call(token: str, body: dict) -> dict:
    req = urllib.request.Request(
        EXPORT_URL,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "x-github-oidc": token},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.load(resp)


def download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=300) as resp:
        return resp.read()


# --- the data branch -------------------------------------------------------------------------


def slug(text: str | None) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:60] or "unknown"


def record_path(row: dict) -> Path:
    folder = "captures" if row["kind"] == "capture" else "comparisons"
    game = slug((row.get("exe") or "").removesuffix(".exe") or row.get("game"))
    return WORK / folder / game / row["contributor"] / f"{row['local_id']}.json"


def load_branch() -> str | None:
    """Unpack the current data branch into WORK; returns its tree hash (None: no branch yet)."""
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    fetch = subprocess.run(["git", "fetch", "--depth", "1", "origin", BRANCH], capture_output=True)
    if fetch.returncode:
        return None
    tar = subprocess.run(["git", "archive", "FETCH_HEAD"], check=True, capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", str(WORK)], input=tar, check=True)
    return run("git", "rev-parse", "FETCH_HEAD^{tree}")


def write_tree() -> str:
    """Stage WORK into a private index and return its tree hash."""
    git_dir = Path(run("git", "rev-parse", "--absolute-git-dir"))
    env = {**os.environ, "GIT_INDEX_FILE": str(git_dir / "data-index")}
    Path(env["GIT_INDEX_FILE"]).unlink(missing_ok=True)
    work = str(WORK.resolve())
    run("git", "-C", work, f"--git-dir={git_dir}", "--work-tree=.", "add", "-A", ".", env=env)
    return run("git", f"--git-dir={git_dir}", "write-tree", env=env)


def publish(tree: str, message: str) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "frametrace-export",
        "GIT_AUTHOR_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
        "GIT_COMMITTER_NAME": "frametrace-export",
        "GIT_COMMITTER_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
    }
    commit = run("git", "commit-tree", tree, "-m", message, env=env)
    run("git", "push", "--force", "origin", f"{commit}:refs/heads/{BRANCH}", capture=False)


# --- CSV releases ------------------------------------------------------------------------------


class Releases:
    def __init__(self, dry: bool):
        self.dry = dry
        self.names: dict[str, set[str]] = {}
        self.tags: list[str] | None = None

    def _list(self) -> list[str]:
        if self.tags is None:
            out = (
                "[]"
                if self.dry
                else run(
                    "gh", "release", "list", "-R", REPO, "--limit", "1000", "--json", "tagName"
                )
            )
            tags = [r["tagName"] for r in json.loads(out or "[]")]
            self.tags = [t for t in tags if t.startswith("csv-")]
        return self.tags

    def _assets(self, tag: str) -> set[str]:
        if tag not in self.names:
            out = run("gh", "release", "view", tag, "-R", REPO, "--json", "assets", check=False)
            assets = json.loads(out or "{}").get("assets", []) if out else []
            self.names[tag] = {a["name"] for a in assets}
        return self.names[tag]

    def target(self, month: str) -> str:
        """The month's release with room left, created when needed."""
        existing = set(self._list())
        n = 1
        while True:
            tag = f"csv-{month}" + (f"-{n}" if n > 1 else "")
            if tag not in existing:
                if not self.dry:
                    run(
                        "gh", "release", "create", tag, "-R", REPO, "--title", f"CSVs {month}",
                        "--notes", "PresentMon CSVs (the game's rows only, gzipped) for the"
                        " captures in the data branch. Each capture's JSON names its asset.",
                    )  # fmt: skip
                self.names[tag] = set()
                self._list().append(tag)
                return tag
            if len(self._assets(tag)) < ASSETS_PER_RELEASE:
                return tag
            n += 1

    def upload(self, data: bytes, name: str, month: str) -> str:
        tag = self.target(month)
        if name not in self._assets(tag) and not self.dry:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / name
                path.write_bytes(data)
                run("gh", "release", "upload", tag, str(path), "-R", REPO, "--clobber")
        self.names[tag].add(name)
        return f"{tag}/{name}"

    def delete_prefix(self, prefix: str) -> int:
        gone = 0
        for tag in self._list():
            for name in sorted(self._assets(tag)):
                if name.startswith(prefix + "_"):
                    if not self.dry:
                        run("gh", "release", "delete-asset", tag, name, "-R", REPO, "-y")
                    self.names[tag].discard(name)
                    gone += 1
        return gone


# --- one export run ----------------------------------------------------------------------------


def export_row(row: dict, releases: Releases) -> dict | None:
    """Write one submission; returns its ack (None when its CSV couldn't be archived)."""
    path = record_path(row)
    old = {}
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            old = {}
    csv = old.get("csv")
    archive = None
    if row.get("csv_url"):
        blob = download(row["csv_url"])
        if hashlib.sha256(blob).hexdigest() != row.get("csv_sha256"):
            log(f"  CSV of {row['local_id']} doesn't match its checksum: kept in the inbox")
        else:
            name = f"{row['contributor']}_{row['local_id']}.csv.gz"
            archive = releases.upload(blob, name, row["updated_at"][:7])
    elif row.get("csv_archive"):
        archive = row["csv_archive"]
    if archive:
        tag, name = archive.split("/", 1)
        csv = {
            "url": f"https://github.com/{REPO}/releases/download/{tag}/{name}",
            "bytes": row.get("csv_bytes"),
            "sha256": row.get("csv_sha256"),
        }
    record = {
        "kind": row["kind"],
        "contributor": row["contributor"],
        "local_id": row["local_id"],
        "schema": row["schema"],
        "app_version": row.get("app_version"),
        "received_at": row["created_at"],
        "updated_at": row["updated_at"],
        "csv": csv,
        "data": row["data"],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    ack = {"id": row["id"], "updated_at": row["updated_at"]}
    if archive and row.get("csv_url"):
        ack["csv_archive"] = archive
    return ack


def apply_deletions(deletions: list[dict], releases: Releases) -> int:
    removed = 0
    for d in deletions:
        who = d["contributor"]
        for folder in WORK.glob(f"*/*/{who}"):
            removed += sum(1 for _ in folder.glob("*.json"))
            shutil.rmtree(folder)
        removed += releases.delete_prefix(who)
    for empty in sorted(WORK.glob("*/*"), reverse=True):  # game folders left empty
        if empty.is_dir() and not any(empty.iterdir()):
            empty.rmdir()
    return removed


def _game_stats(caps: list[Path], comps: list[Path]) -> dict:
    """Per game: what the Research Bot and people browsing need first. Benchmarks are the
    users' tags for their scenes (user.benchmark); bundles are runs a user put together as
    repeats of one setup (user.bundle)."""
    out: dict[str, dict] = {}
    for path in sorted(caps):
        try:
            data = json.loads(path.read_text(encoding="utf-8")).get("data") or {}
        except (OSError, ValueError):
            continue
        user = data.get("user") or {}
        g = out.setdefault(
            path.parent.parent.name,
            {"captures": 0, "comparisons": 0, "benchmarks": Counter(), "untagged": 0,
             "bundles": set(), "bundled": 0, "labelled": 0, "settings_read": 0, "builds": set()},
        )  # fmt: skip
        g["captures"] += 1
        tag = " ".join(str(user.get("benchmark") or "").split())
        if tag:
            g["benchmarks"][tag] += 1
        else:
            g["untagged"] += 1
        if user.get("bundle"):
            g["bundled"] += 1
            g["bundles"].add((path.parent.name, user["bundle"]))
        g["labelled"] += bool(user.get("label"))
        g["settings_read"] += bool((data.get("game_settings") or {}).get("settings"))
        build = (data.get("game") or {}).get("build")
        if build:
            g["builds"].add(str(build))
    for path in comps:
        game = path.parent.parent.name
        if game in out:
            out[game]["comparisons"] += 1
    for g in out.values():
        g["benchmarks"] = dict(sorted(g["benchmarks"].items(), key=lambda kv: (-kv[1], kv[0])))
        g["bundles"] = len(g["bundles"])
        g["builds"] = sorted(g["builds"])
    return dict(sorted(out.items(), key=lambda kv: (-kv[1]["captures"], kv[0])))


def write_stats() -> dict:
    caps = list(WORK.glob("captures/*/*/*.json"))
    comps = list(WORK.glob("comparisons/*/*/*.json"))
    newest = ""
    for path in caps + comps:
        try:
            newest = max(newest, json.loads(path.read_text(encoding="utf-8"))["updated_at"])
        except (OSError, ValueError, KeyError):
            continue
    stats = {
        "updated": newest or None,  # the newest submission, so an idle day changes nothing
        "captures": len(caps),
        "comparisons": len(comps),
        "contributors": len({p.parent.name for p in caps + comps}),
        "games": dict(Counter(p.parent.parent.name for p in caps).most_common()),
        "by_game": _game_stats(caps, comps),
    }
    (WORK / "stats.json").write_text(json.dumps(stats, indent=1) + "\n", encoding="utf-8")
    (WORK / "README.md").write_text(
        "# Frametrace dataset (data branch)\n\n"
        f"See [the main branch](https://github.com/{REPO}) for what's in here and how to use it.\n"
        "This branch is rewritten by every export: it has one commit and no history.\n",
        encoding="utf-8",
    )
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Export the Frametrace inbox to the data branch.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    token = oidc_token()
    before = load_branch()
    releases = Releases(args.dry_run)
    acks: list[dict] = []
    deletions: list[dict] = []
    after = ""
    for _ in range(MAX_PAGES):
        page = call(token, {"action": "pull", "limit": PAGE, "after": after})
        deletions = page.get("deletions", [])
        rows = page.get("rows", [])
        for row in rows:
            ack = export_row(row, releases)
            if ack:
                acks.append(ack)
        log(f"pulled {len(rows)} rows")
        if len(rows) < PAGE:
            break
        after = rows[-1]["updated_at"]
    removed = apply_deletions(deletions, releases)
    stats = write_stats()
    log(f"removed {removed} files and assets of {len(deletions)} deleted contributors")
    log(json.dumps(stats))

    tree = write_tree()
    if args.dry_run:
        log("dry run: nothing pushed or acknowledged")
        return 0
    if tree != before:
        when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        n_caps, n_comps = stats["captures"], stats["comparisons"]
        publish(tree, f"Dataset as of {when} UTC: {n_caps} captures, {n_comps} comparisons")
        log("pushed the data branch")
    acked = 0
    for i in range(0, len(acks), 500):
        done = call(token, {"action": "ack", "rows": acks[i : i + 500]})
        acked += int(done.get("acked") or 0)
    log(f"acknowledged {acked} of {len(acks)}")
    if acked < len(acks):
        # Rows changed since the pull are refused on purpose (they go out next run); many
        # refusals mean the inbox and this script disagree: fail so GitHub sends a notice.
        log(f"{len(acks) - acked} rows weren't acknowledged; they'll be exported again")
        return 1 if acked < len(acks) * 0.9 else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
