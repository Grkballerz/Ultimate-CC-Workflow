---
name: Feature request
about: Suggest an addition or change
labels: enhancement
---

## The problem you're trying to solve

<!-- Concrete user pain, not the proposed solution. -->

## What you've considered

<!-- Workarounds, alternatives, why none of them are enough. -->

## Proposed change (optional)

<!-- If you have one. Otherwise just describe the desired behavior. -->

## Fit with UCW design principles

- Does this make Knowledge the canonical truth and Memory the long tail, or
  does it blur the line? (Blurring = harder to merge.)
- Does it run within a hook's <2s budget if it touches the inner loop?
- Does it stay stdlib-only on the FTS5 path, or does it need a new optional
  dependency in `memory/pyproject.toml` extras?
