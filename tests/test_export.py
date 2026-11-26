"""tools/export.py against a fake inbox and a local bare remote (no network, no gh)."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import export  # noqa: E402

CSV = b"\x1f\x8bfake-gzip"


def git(*args, cwd) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return out.stdout


def row(n: int, who="a1b2c3d4e5f60718", kind="capture", csv=True) -> dict:
    return {
        "id": f"id-{n}",
        "contributor": who,
        "kind": kind,
        "local_id": f"20261126-1{n:05d}-cs2" if kind == "capture" else f"x{n}~y{n}",
        "schema": 1,
        "app_version": "0.0.1",
        "game": "Counter-Strike 2",
        "exe": "cs2.exe",
        "created_at": "2026-11-26T10:00:00+00:00",
        "updated_at": f"2026-11-26T10:00:{n:02d}.000000+00:00",
        "data": {"n": n},
        "csv_sha256": hashlib.sha256(CSV).hexdigest() if csv else None,
        "csv_bytes": len(CSV) if csv else None,
        "csv_url": "https://example.invalid/csv" if csv else None,
        "csv_archive": None,
    }


class FakeInbox:
    def __init__(self, rows, deletions=()):
        self.rows, self.deletions, self.acks = list(rows), list(deletions), []

    def __call__(self, token, body):
        assert token == "token"
        if body["action"] == "pull":
            after = body.get("after") or ""
            rows = [r for r in self.rows if r["updated_at"] > after][: body["limit"]]
            return {"rows": rows, "deletions": self.deletions}
        self.acks += body["rows"]
        acked = {a["id"] for a in body["rows"]}
        self.rows = [r for r in self.rows if r["id"] not in acked]
        return {"ok": True, "acked": len(body["rows"])}


class FakeReleases:
    assets: dict[str, set[str]] = {}

    def __init__(self, dry):
        pass

    def upload(self, data, name, month):
        tag = f"csv-{month}"
        self.assets.setdefault(tag, set()).add(name)
        return f"{tag}/{name}"

    def delete_prefix(self, prefix):
        gone = 0
        for names in self.assets.values():
            for n in [n for n in names if n.startswith(prefix + "_")]:
                names.discard(n)
                gone += 1
        return gone


@pytest.fixture
def repo(tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, capture_output=True)
    git("config", "user.email", "t@example.invalid", cwd=work)
    git("config", "user.name", "t", cwd=work)
    (work / "README.md").write_text("main\n")
    git("add", ".", cwd=work)
    git("commit", "-qm", "main", cwd=work)
    git("push", "-q", "origin", "HEAD:main", cwd=work)
    monkeypatch.chdir(work)
    monkeypatch.setattr(export, "WORK", Path("dataset"))
    monkeypatch.setattr(export, "oidc_token", lambda: "token")
    monkeypatch.setattr(export, "download", lambda url: CSV)
    monkeypatch.setattr(export, "Releases", FakeReleases)
    FakeReleases.assets = {}
    return origin


def branch_files(origin: Path) -> list[str]:
    return sorted(git("ls-tree", "-r", "--name-only", "data", cwd=origin).split())


def test_export_publishes_rows_archives_csvs_and_acks(repo, monkeypatch):
    inbox = FakeInbox([row(1), row(2), row(3, kind="comparison", csv=False)])
    monkeypatch.setattr(export, "call", inbox)
    assert export.main([]) == 0
    files = branch_files(repo)
    assert "captures/cs2/a1b2c3d4e5f60718/20261126-100001-cs2.json" in files
    assert "comparisons/cs2/a1b2c3d4e5f60718/x3~y3.json" in files
    assert "stats.json" in files and "README.md" in files
    rec = json.loads(
        git("show", "data:captures/cs2/a1b2c3d4e5f60718/20261126-100001-cs2.json", cwd=repo)
    )
    assert rec["csv"]["url"].endswith("/csv-2026-11/a1b2c3d4e5f60718_20261126-100001-cs2.csv.gz")
    assert rec["data"] == {"n": 1}
    assert len(inbox.acks) == 3 and inbox.acks[0]["csv_archive"].startswith("csv-2026-11/")
    assert "csv_archive" not in inbox.acks[2]  # the comparison has no CSV
    stats = json.loads(git("show", "data:stats.json", cwd=repo))
    assert stats["captures"] == 2 and stats["comparisons"] == 1 and stats["contributors"] == 1
    assert len(git("rev-list", "data", cwd=repo).split()) == 1


def test_pages_through_more_than_one_pull(repo, monkeypatch):
    monkeypatch.setattr(export, "PAGE", 2)
    inbox = FakeInbox([row(n) for n in range(1, 6)])
    monkeypatch.setattr(export, "call", inbox)
    export.main([])
    assert len([f for f in branch_files(repo) if f.startswith("captures/")]) == 5


def test_deletions_remove_files_and_assets_and_leave_no_history(repo, monkeypatch):
    other = "ffffeeeeddddcccc"
    monkeypatch.setattr(export, "call", FakeInbox([row(1), row(2, who=other)]))
    export.main([])
    assert any(other in f for f in branch_files(repo))
    monkeypatch.setattr(export, "call", FakeInbox([], deletions=[{"contributor": other}]))
    export.main([])
    files = branch_files(repo)
    assert not any(other in f for f in files)
    assert any("a1b2c3d4e5f60718" in f for f in files)
    assert not any(n.startswith(other) for names in FakeReleases.assets.values() for n in names)
    assert len(git("rev-list", "data", cwd=repo).split()) == 1  # a new root, the old one is gone


def test_an_idle_run_pushes_nothing(repo, monkeypatch):
    monkeypatch.setattr(export, "call", FakeInbox([row(1)]))
    export.main([])
    first = git("rev-parse", "data", cwd=repo)
    monkeypatch.setattr(export, "call", FakeInbox([]))
    export.main([])
    assert git("rev-parse", "data", cwd=repo) == first


def test_a_bad_checksum_keeps_the_csv_in_the_inbox(repo, monkeypatch):
    bad = row(1)
    bad["csv_sha256"] = "0" * 64
    inbox = FakeInbox([bad])
    monkeypatch.setattr(export, "call", inbox)
    export.main([])
    rec = json.loads(
        git("show", "data:captures/cs2/a1b2c3d4e5f60718/20261126-100001-cs2.json", cwd=repo)
    )
    assert rec["csv"] is None and "csv_archive" not in inbox.acks[0]


def test_a_run_fails_when_the_inbox_refuses_the_acks(repo, monkeypatch):
    inbox = FakeInbox([row(1), row(2)])

    def refusing(token, body):
        reply = inbox(token, body)
        return {"ok": True, "acked": 0} if body["action"] == "ack" else reply

    monkeypatch.setattr(export, "call", refusing)
    assert export.main([]) == 1
