---
name: tidepool-dev-release
description: How code is developed and released at Tidepool: repositories and owners, branches, flags, build, test and rollout. Read before branching, committing, building, opening a review, creating a flag, or asking for a rollout.
---

# Tidepool: development and release

## Hard rules

- Every user-visible change ships behind a flag that is off by default (Mara, #4361, 9 March 2026).
- A flag is created in all three environments at once: staging, beta, production (tested 11 March 2026;
  N-20260311-02).

## Repositories and owners

| Repository | Owner | What it is | Default branch |
| --- | --- | --- | --- |
| app | Mara | The mobile app | main |
| tides-api | Okafor | Tide predictions and alert scheduling | main |

## Branches and versions

Branch from `main` as `req-<number>-<short-name>`. One request per branch (Mara, verbal, 9 March 2026).

## Build and test

`make check` in either repository runs the unit tests and the linter. A passing run ends with `OK` and a test
count; record the count in the day's log (tested 10 March 2026).

## Release

The steps are the F process in [state-machines.md](../tidepool-team/references/state-machines.md).

## Where to look when something breaks

The flag dashboard shows the error rate per flag. Okafor owns it.

Open questions: whether `tides-api` changes also need a flag.
