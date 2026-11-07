# graphify mirror

This repository holds a copy of every branch and tag [Graphify-Labs/graphify](https://github.com/Graphify-Labs/graphify) has published, so we can still build and run graphify if upstream deletes, rewrites, relicenses or withdraws it. Nothing of ours is built from upstream's branches here.

A daily workflow (`.github/workflows/mirror.yml`, on this `mirror` branch) follows upstream, but never into losing anything:

| Upstream does | The mirror does |
|---|---|
| Adds a branch or tag | Adds it |
| Moves a branch forward | Moves it forward |
| Deletes a branch or tag | Keeps it |
| Rewrites a branch (force-push) | Keeps our old tip as `kept/<branch>/<date>-<sha7>`, then follows |
| Points an existing tag elsewhere | Keeps our tag; keeps theirs as `kept/upstream/<tag>-<sha7>` |
| Disappears, or cannot be read | Changes nothing, fails, and opens an issue |

Branches of our own are never written from upstream: `mirror` (this branch, the default), `rhodium/*` and `kept/*`. Our earlier packaging commits on `v8` (the `[openai]` extra and the image build) are kept as `kept/v8/2026-11-07-4da13ee`.

Upstream's own workflows are disabled here after every run, so its CI, publishing and bots never act on this repository.

The workflow pushes with `MIRROR_PUSH_TOKEN`, a fine-grained token for this repository only with Contents and Workflows read and write (a push that adds upstream's workflow files needs Workflows).

The requirements are in `mirror/idd` (`tl -C mirror/idd check`); the tests are `python3 -m unittest discover -s mirror -p 'test_*.py'`.

graphify is licensed by its authors: MIT up to 0.9.24, Apache-2.0 from 0.9.25 (22 July 2026). Each copy here keeps the licence it was published under.
