#!/usr/bin/env python3
"""Mirror an upstream git repository into this one without ever losing a ref.

The mirror follows upstream: a new branch or tag is created, a branch that
moves forward is moved forward (REQ-0001, REQ-0002). It never follows upstream
into losing something:

- a branch or tag upstream deletes is kept (REQ-0003);
- a branch upstream rewrites (a force-push) follows upstream, but the tip it
  had here is kept first as ``kept/<branch>/<date>-<sha7>`` (REQ-0004);
- a tag is never moved: when upstream points a tag elsewhere, ours stays and
  theirs is kept as ``kept/upstream/<tag>-<sha7>`` (REQ-0005);
- when upstream cannot be read, or has no branches, nothing here changes and
  the run fails (REQ-0006);
- branches of our own (``mirror``, ``rhodium/*``, ``kept/*``) are never
  written from upstream (REQ-0007).

A run that finds nothing new pushes nothing (REQ-0008).

    mirror.py --upstream URL --target URL --work DIR [--dry-run]

The target URL may carry a token; it is never printed.
"""

from __future__ import annotations

import argparse
import datetime
import os
import subprocess
import sys
from dataclasses import dataclass, field

OWN = ("mirror",)
OWN_PREFIXES = ("rhodium/", "kept/")
GONE = 2


class UpstreamGone(Exception):
    """Upstream could not be read, or listed no branches."""


def own(branch: str) -> bool:
    return branch in OWN or branch.startswith(OWN_PREFIXES)


def git(*args: str, cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=check, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )


def ls_remote(url: str) -> tuple[dict[str, str], dict[str, str]]:
    """The heads and tags a remote lists, each name to the object it names."""
    out = git("ls-remote", "--heads", "--tags", url, check=False)
    if out.returncode != 0:
        raise UpstreamGone(" ".join(out.stderr.split()) or "ls-remote failed")
    heads: dict[str, str] = {}
    tags: dict[str, str] = {}
    for line in out.stdout.splitlines():
        sha, ref = line.split("\t", 1)
        if ref.endswith("^{}"):
            continue
        if ref.startswith("refs/heads/"):
            heads[ref[len("refs/heads/"):]] = sha
        elif ref.startswith("refs/tags/"):
            tags[ref[len("refs/tags/"):]] = sha
    return heads, tags


@dataclass
class Plan:
    """What one run pushes, and what it saw and left alone."""

    refspecs: list[str] = field(default_factory=list)
    leases: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    forwarded: list[str] = field(default_factory=list)
    rewritten: list[tuple[str, str]] = field(default_factory=list)
    retagged: list[tuple[str, str]] = field(default_factory=list)
    gone_upstream: list[str] = field(default_factory=list)
    skipped_own: list[str] = field(default_factory=list)

    def empty(self) -> bool:
        return not self.refspecs


def plan(
    up_heads: dict[str, str], up_tags: dict[str, str],
    our_heads: dict[str, str], our_tags: dict[str, str],
    is_ancestor, today: str,
) -> Plan:
    """Decide every push from the two listings. ``is_ancestor(a, b)`` says a is in b's history."""
    p = Plan()
    for branch, theirs in sorted(up_heads.items()):
        if own(branch):
            p.skipped_own.append(branch)
            continue
        ours = our_heads.get(branch)
        if ours is None:
            p.refspecs.append(f"refs/up/heads/{branch}:refs/heads/{branch}")
            p.created.append(branch)
        elif ours == theirs:
            continue
        elif is_ancestor(ours, theirs):
            p.refspecs.append(f"refs/up/heads/{branch}:refs/heads/{branch}")
            p.forwarded.append(branch)
        else:
            kept = f"kept/{branch}/{today}-{ours[:7]}"
            if kept not in our_heads:
                p.refspecs.append(f"{ours}:refs/heads/{kept}")
            p.refspecs.append(f"+refs/up/heads/{branch}:refs/heads/{branch}")
            p.leases.append(f"--force-with-lease=refs/heads/{branch}:{ours}")
            p.rewritten.append((branch, kept))
    for tag, theirs in sorted(up_tags.items()):
        if tag.startswith("kept/"):
            continue
        ours = our_tags.get(tag)
        if ours is None:
            p.refspecs.append(f"refs/up/tags/{tag}:refs/tags/{tag}")
            p.created.append(f"tag {tag}")
        elif ours != theirs:
            kept = f"kept/upstream/{tag}-{theirs[:7]}"
            if kept not in our_tags:
                p.refspecs.append(f"refs/up/tags/{tag}:refs/tags/{kept}")
                p.retagged.append((tag, kept))
    p.gone_upstream = sorted(
        [b for b in our_heads if b not in up_heads and not own(b)]
        + [f"tag {t}" for t in our_tags if t not in up_tags and not t.startswith("kept/")]
    )
    return p


def run(upstream: str, target: str, work: str, dry_run: bool, today: str) -> Plan:
    up_heads, up_tags = ls_remote(upstream)
    if not up_heads:
        raise UpstreamGone("upstream lists no branches")
    try:
        our_heads, our_tags = ls_remote(target)
    except UpstreamGone as e:
        raise RuntimeError(f"cannot read the target: {e}") from None

    os.makedirs(work, exist_ok=True)
    if not os.path.exists(os.path.join(work, "HEAD")):
        git("init", "--bare", "-q", work)
    fetched = git("fetch", "--no-tags", "-q", upstream,
                  "+refs/heads/*:refs/up/heads/*", "+refs/tags/*:refs/up/tags/*",
                  cwd=work, check=False)
    if fetched.returncode != 0:
        raise UpstreamGone("fetch from upstream failed")
    if our_heads:
        git("fetch", "--no-tags", "-q", target, "+refs/heads/*:refs/ours/heads/*", cwd=work)

    def is_ancestor(a: str, b: str) -> bool:
        return git("merge-base", "--is-ancestor", a, b, cwd=work, check=False).returncode == 0

    p = plan(up_heads, up_tags, our_heads, our_tags, is_ancestor, today)
    if p.empty() or dry_run:
        return p
    pushed = git("push", "--atomic", "--porcelain", *p.leases, target, *p.refspecs, cwd=work, check=False)
    if pushed.returncode != 0:
        raise RuntimeError("push refused; nothing was changed:\n" + pushed.stdout + pushed.stderr.replace(target, "<target>"))
    return p


def report(p: Plan, dry_run: bool) -> str:
    lines = ["## Mirror" + (" (dry run, nothing pushed)" if dry_run else "")]
    if p.empty():
        lines.append("Nothing new upstream.")
    sections = [
        ("Created", p.created),
        ("Moved forward", p.forwarded),
        ("Rewritten upstream, old tip kept", [f"{b} (kept as {k})" for b, k in p.rewritten]),
        ("Tag moved upstream, ours kept", [f"{t} (theirs kept as {k})" for t, k in p.retagged]),
        ("Gone upstream, kept here", p.gone_upstream),
        ("Upstream branch in our own names, not written", p.skipped_own),
    ]
    for title, items in sections:
        if items:
            lines.append(f"\n**{title}** ({len(items)})")
            lines.extend(f"- {i}" for i in items[:200])
            if len(items) > 200:
                lines.append(f"- and {len(items) - 200} more")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    a = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    a.add_argument("--upstream", required=True)
    a.add_argument("--target", required=True)
    a.add_argument("--work", required=True)
    a.add_argument("--dry-run", action="store_true")
    args = a.parse_args(argv)
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    try:
        p = run(args.upstream, args.target, args.work, args.dry_run, today)
    except UpstreamGone as e:
        print(f"Upstream cannot be read ({e}); nothing here was changed.", file=sys.stderr)
        return GONE
    text = report(p, args.dry_run)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
