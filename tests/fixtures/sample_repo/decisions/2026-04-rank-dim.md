---
title: ALS rank dimension — 128 over 64
status: accepted
date: 2026-04-12
supersedes: adr:decisions/2026-02-rank-baseline.md
decides:
  - param:src/sample/model.py::M.fit::rank
  - param:src/sample/model.py::M.fit::learning_rate
---

# ALS rank dimension — 128 over 64

Bumps rank from 64 to 128 to give headroom on long-tail items.

See implementation in [model.py](src/sample/model.py).
