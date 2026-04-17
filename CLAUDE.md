# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the script

```bash
conda activate base
python qa_report.py
```

Dependencies are managed via conda (PyPI is blocked on this machine):

```bash
conda install -c conda-forge anthropic requests python-dotenv
```

## Environment setup

Copy `.env.example` to `.env` and fill in:
- `ATLASSIAN_EMAIL` — Atlassian account email
- `ATLASSIAN_API_TOKEN` — API token from https://id.atlassian.com/manage-profile/security/api-tokens
- `ANTHROPIC_API_KEY` — optional; only needed for the MCP path (currently unavailable due to org policy)
- `ZEPHYR_SCALE_API_KEY` — Zephyr Scale API key for the Xander/HEAL board (see below)

## Zephyr Scale integration

The Xander/HEAL board has `zephyr_project_key: "HEAL"` set, enabling automatic test case fetching.

**How to get the API key:**
1. Go to `https://futuresecureai.atlassian.net`
2. Top nav → **Apps** → **Zephyr Scale**
3. Click the settings/gear icon → **API Access Tokens**
4. Generate a new token and copy it
5. Add to `.env` as `ZEPHYR_SCALE_API_KEY=<token>`

**What it fetches:** test cases linked to Jira issues in the current active sprint (sprint-scoped,
not folder-based). Counts executed (Pass/Fail/Blocked) vs outstanding (Unexecuted/any other).
The user sees the pre-filled numbers and can press Enter to accept or type to override.

Zephyr fetch is attempted inside `fetch_via_rest` after `filtered` sprint issues are determined.
If `ZEPHYR_SCALE_API_KEY` is missing or the API call fails, the script falls back silently to
manual entry (no pre-fills).

## Architecture

Single-file script (`qa_report.py`). Flow:

1. **Config** — `BOARDS` list defines two Jira boards with `base_url`, `board_id`, `target_epics`, and `customer`.
2. **Fetch** — For each board: tries Atlassian MCP via Anthropic SDK (`fetch_via_mcp`), falls back to direct Jira REST API (`fetch_via_rest`), falls back to manual prompts (`prompt_auto_fields`).
3. **Epic detection** — `fetch_via_rest` queries `/rest/api/3/search/jql` (note: old `/rest/api/3/search` returns 410). Detects epics via the `parent` field (Next-gen Jira) or `customfield_10014` Epic Link (Classic Jira). AICW keywords are matched from `target_epics` against both matched epic names and the sprint name.
4. **Prompts** — `prompt_board_fields(aicw)` collects test case counts, defects, and blockers per board (prompts include the AICW name for clarity). `prompt_uat_fields()` is called once after all boards for UAT details.
5. **Output** — `build_combined_email()` renders a single email with one section per board plus a shared UAT block at the bottom; `main()` prints the combined email.

## Board configuration

| Board | URL | Board ID | Epics tracked |
|---|---|---|---|
| Healius / FDW | healius-digital.atlassian.net | 402 | Reva, Melinda, Dixie |
| Xander / HEAL | futuresecureai.atlassian.net | 3426 | Xander |

Both boards hardcode `customer = "Healius"`.

## Key behaviours

- **AICW field**: built from `target_epics` keywords matched in epic titles **or** the sprint name (e.g. "Melinda" from sprint "FDW UC 1.2 Melinda Sprint 11"), joined with `/`.
- **Status mapping**: `READY FOR QA` + `IN TESTING` → Pending; `AWAITING DEPLOYMENT` + `DONE` → Tested.
- **Epic filter fallback**: if no target epics are found, all non-Epic sprint issues are counted and a warning is printed with the available epic names.
- **UAT section**: omitted entirely (shows `UAT Status – N/A`) when user answers `n`.
