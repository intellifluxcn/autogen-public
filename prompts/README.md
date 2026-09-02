# Prompts

This directory collects the LLM prompts used by the pipeline — the "prompts"
component promised in the paper's data availability statement.

## 1. Prompts that can be extracted whole (read-only copies here)

| File | Stage | Source |
| --- | --- | --- |
| `find-prescreen-system.md` | Find (relevance prescreen) | `find/prescreen.py` L61 |
| `find-prescreen-user-template.md` | Find (prescreen user template) | `find/prescreen.py` L78 |
| `download-embedded-data-extract.md` | Download (extraction from tables/figures) | `download/embedded_data_team.py` L34 |
| `qualify-known-db-waiver.md` | Qualify (known-database waiver) | `qualify/team.py` L225 |
| `qualify-validation-prompt.md` | Qualify (main validation prompt) | `qualify/validation_prompt.md` (already a standalone file) |

> ⚠️ **These copies are for reading and citation only; the code is
> authoritative at runtime.** If the two ever disagree, the code wins — please
> open an issue.

## 2. Prompts that cannot be extracted as files (assembled inline in code)

The prompts below are assembled at runtime with f-strings, because they
interpolate the paper text, deterministic scan hints, dataset listings, and so
on. **None of them exists as a single self-contained string.** Rather than give
the false impression that everything has been exported, here are their exact
locations — please read them in the code:

| Location | Size | What it is |
| --- | --- | --- |
| `analyze/team.py` L339 | ~7.9 K chars | Analyze stage main prompt (Data Extraction Specialist) |
| `analyze/team.py` L359 | ~6.8 K chars | Analyze stage extraction task list (sequencing / drug-response branches) |
| `analyze/team.py` L344 | ~0.7 K chars | Injected block for deterministic scan hints |
| `analyze/team.py` L319 | ~0.4 K chars | Closing-tag sanitisation for untrusted content |
| `download/plan_adapter.py` L149 | ~1.1 K chars | Scientific data guidelines for the download plan |
| `qualify/team.py` L294 | ~0.6 K chars | Reference gene file description block |

## 3. Search strategy

Beyond the prompts, the Find stage's search strategy — the three-concept
AND-of-ORs query, a ready-to-use query, and the recall knobs — is documented in
the repository [`README`](../README.md#writing-a-research-query).
