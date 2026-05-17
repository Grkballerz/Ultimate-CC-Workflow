---
description: Run the planner — scope + plan, presented for approval before any code is touched.
argument-hint: "<goal>"
---

The goal: $ARGUMENTS

Invoke the **planner** subagent.

Wait for user approval at Scope phase boundary, then again at Plan phase boundary. Do not proceed to Build until both are approved.

On approval, write the final plan to `.ucw/state/plan.md` so the implementer can pick it up.
