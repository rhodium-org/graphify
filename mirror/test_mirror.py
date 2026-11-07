"""The mirror against real git: an upstream and a target, both bare repositories on disk."""

import os
import shutil
import subprocess
import tempfile
import unittest

import mirror

TODAY = "2026-11-07"


def sh(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.strip()


class Mirror(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.up = os.path.join(self.root, "upstream.git")
        self.target = os.path.join(self.root, "target.git")
        self.work = os.path.join(self.root, "work")
        self.dev = os.path.join(self.root, "dev")
        sh("init", "--bare", "-q", "-b", "v8", self.up)
        sh("init", "--bare", "-q", self.target)
        sh("init", "-q", "-b", "v8", self.dev)
        sh("config", "user.email", "t@example.com", cwd=self.dev)
        sh("config", "user.name", "t", cwd=self.dev)
        sh("remote", "add", "origin", self.up, cwd=self.dev)
        self.commit("one")
        sh("tag", "-a", "v0.1", "-m", "v0.1", cwd=self.dev)
        sh("push", "-q", "origin", "v8", "v0.1", cwd=self.dev)

    def tearDown(self):
        shutil.rmtree(self.root)

    def commit(self, text):
        with open(os.path.join(self.dev, "f"), "w") as f:
            f.write(text)
        sh("add", "f", cwd=self.dev)
        sh("commit", "-q", "-m", text, cwd=self.dev)
        return sh("rev-parse", "HEAD", cwd=self.dev)

    def mirror(self, dry_run=False):
        return mirror.run(self.up, self.target, self.work, dry_run, TODAY)

    def refs(self, repo):
        heads, tags = mirror.ls_remote(repo)
        return heads, tags

    # TEST-0001
    def test_new_branches_and_tags_are_created(self):
        self.mirror()
        heads, tags = self.refs(self.target)
        self.assertEqual(heads, self.refs(self.up)[0])
        self.assertEqual(tags, self.refs(self.up)[1])

    # TEST-0002
    def test_a_branch_that_moves_forward_is_followed(self):
        self.mirror()
        two = self.commit("two")
        sh("push", "-q", "origin", "v8", cwd=self.dev)
        p = self.mirror()
        self.assertEqual(p.forwarded, ["v8"])
        self.assertEqual(self.refs(self.target)[0]["v8"], two)

    # TEST-0003
    def test_what_upstream_deletes_is_kept(self):
        sh("checkout", "-q", "-b", "feature", cwd=self.dev)
        self.commit("feature")
        sh("push", "-q", "origin", "feature", cwd=self.dev)
        self.mirror()
        sh("push", "-q", "origin", "--delete", "feature", "v0.1", cwd=self.dev)
        p = self.mirror()
        heads, tags = self.refs(self.target)
        self.assertIn("feature", heads)
        self.assertIn("v0.1", tags)
        self.assertEqual(p.gone_upstream, ["feature", "tag v0.1"])

    # TEST-0004
    def test_a_rewritten_branch_follows_upstream_and_keeps_its_old_tip(self):
        self.mirror()
        old = self.refs(self.target)[0]["v8"]
        sh("commit", "-q", "--amend", "-m", "rewritten", cwd=self.dev)
        new = sh("rev-parse", "HEAD", cwd=self.dev)
        sh("push", "-q", "-f", "origin", "v8", cwd=self.dev)
        p = self.mirror()
        heads = self.refs(self.target)[0]
        kept = f"kept/v8/{TODAY}-{old[:7]}"
        self.assertEqual(heads["v8"], new)
        self.assertEqual(heads[kept], old)
        self.assertEqual(p.rewritten, [("v8", kept)])

    # TEST-0005
    def test_our_own_commits_on_a_mirrored_branch_are_kept(self):
        self.mirror()
        ours = os.path.join(self.root, "ours")
        sh("clone", "-q", "-b", "v8", self.target, ours)
        sh("-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q",
           "--allow-empty", "-m", "ours", cwd=ours)
        mine = sh("rev-parse", "HEAD", cwd=ours)
        sh("push", "-q", "origin", "v8", cwd=ours)
        self.commit("upstream moves on")
        sh("push", "-q", "origin", "v8", cwd=self.dev)
        self.mirror()
        heads = self.refs(self.target)[0]
        self.assertEqual(heads["v8"], self.refs(self.up)[0]["v8"])
        self.assertEqual(heads[f"kept/v8/{TODAY}-{mine[:7]}"], mine)

    # TEST-0006
    def test_a_tag_is_never_moved(self):
        self.mirror()
        ours = self.refs(self.target)[1]["v0.1"]
        self.commit("two")
        sh("tag", "-f", "-a", "v0.1", "-m", "moved", cwd=self.dev)
        sh("push", "-q", "-f", "origin", "v0.1", cwd=self.dev)
        theirs = self.refs(self.up)[1]["v0.1"]
        p = self.mirror()
        tags = self.refs(self.target)[1]
        kept = f"kept/upstream/v0.1-{theirs[:7]}"
        self.assertEqual(tags["v0.1"], ours)
        self.assertEqual(tags[kept], theirs)
        self.assertEqual(p.retagged, [("v0.1", kept)])
        self.assertTrue(self.mirror().empty())

    # TEST-0007
    def test_an_upstream_that_cannot_be_read_changes_nothing(self):
        self.mirror()
        before = self.refs(self.target)
        shutil.rmtree(self.up)
        self.assertEqual(mirror.main(["--upstream", self.up, "--target", self.target,
                                      "--work", self.work]), mirror.GONE)
        self.assertEqual(self.refs(self.target), before)

    # TEST-0008
    def test_an_upstream_with_no_branches_changes_nothing(self):
        self.mirror()
        before = self.refs(self.target)
        empty = os.path.join(self.root, "empty.git")
        sh("init", "--bare", "-q", empty)
        with self.assertRaises(mirror.UpstreamGone):
            mirror.run(empty, self.target, self.work, False, TODAY)
        self.assertEqual(self.refs(self.target), before)

    # TEST-0009
    def test_our_own_branch_names_are_never_written_from_upstream(self):
        ours = os.path.join(self.root, "ours")
        sh("init", "-q", "-b", "mirror", ours)
        sh("-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q",
           "--allow-empty", "-m", "the mirror's own", cwd=ours)
        mine = sh("rev-parse", "HEAD", cwd=ours)
        sh("push", "-q", self.target, "mirror", "mirror:rhodium/packaging", cwd=ours)
        sh("push", "-q", "origin", "v8:mirror", "v8:rhodium/packaging", cwd=self.dev)
        p = self.mirror()
        heads = self.refs(self.target)[0]
        self.assertEqual(heads["mirror"], mine)
        self.assertEqual(heads["rhodium/packaging"], mine)
        self.assertEqual(p.skipped_own, ["mirror", "rhodium/packaging"])

    # TEST-0010
    def test_a_second_run_pushes_nothing(self):
        self.mirror()
        self.assertTrue(self.mirror().empty())

    # TEST-0011
    def test_a_dry_run_pushes_nothing(self):
        p = self.mirror(dry_run=True)
        self.assertEqual(p.created, ["v8", "tag v0.1"])
        self.assertEqual(self.refs(self.target), ({}, {}))


if __name__ == "__main__":
    unittest.main()
