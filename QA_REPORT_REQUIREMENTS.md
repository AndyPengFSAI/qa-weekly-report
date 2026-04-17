# QA Weekly Status Report Automation — Requirements

## Overview

A Python script that automatically generates a weekly QA status email by pulling sprint data from Jira via the REST API, prompting the user for fields that cannot be automated, and printing a single formatted, copy-paste-ready email to the terminal.

---

## Tech Stack

- **Language:** Python
- **Jira Integration:** Atlassian REST API (Basic Auth) with Atlassian MCP as optional primary path
- **Output:** Formatted email printed to terminal (stdout)

---

## Jira Configuration

Two boards are tracked. Both use the same Atlassian credentials.

| Board | Base URL | Board ID | Epics tracked | Customer |
|---|---|---|---|---|
| Healius / FDW | `https://healius-digital.atlassian.net` | `402` | Reva, Melinda, Dixie | Healius |
| Xander / HEAL | `https://futuresecureai.atlassian.net` | `3426` | Xander | Healius |

### Environment Variables Required

```bash
ATLASSIAN_EMAIL=your@email.com
ATLASSIAN_API_TOKEN=your_api_token   # from https://id.atlassian.com/manage-profile/security/api-tokens
ANTHROPIC_API_KEY=your_anthropic_key # optional — MCP path only; currently unavailable due to org policy
```

---

## Epic Filter

Each board has a `target_epics` list. Only tickets belonging to those epics are counted. Matching is done by checking whether a keyword (e.g. "Reva") appears in the epic title **or** the sprint name. Tickets in other epics are excluded.

If no target-epic tickets are found, all non-Epic sprint issues are counted and a warning listing available epics is printed.

---

## Jira Status Mappings

| Board Status | Report Field |
|---|---|
| `READY FOR QA` | Pending Testing |
| `IN TESTING` | Pending Testing |
| `AWAITING DEPLOYMENT` | Tested (QA complete) |
| `DONE` | Tested (QA complete) |
| `TO DO`, `IN PROGRESS`, `PEER REVIEW`, `BLOCKED` | Not counted in QA metrics |

---

## Data Sources

### Auto-pulled from Jira (per board)

| Report Field | Jira Query Logic |
|---|---|
| **Team Member** | Authenticated user's display name (`/rest/api/3/myself`) |
| **AICW / Customer** | `target_epics` keywords matched in epic titles or sprint name, joined with `/`, plus hardcoded customer (e.g. `Reva/Melinda – Healius`) |
| **Sprint** | Active sprint name for the board |
| **Reporting Week** | Auto-derived from current date (Monday–Friday of current week) |
| **Total Tickets** | COUNT all sprint issues matching target epics |
| **Pending Testing** | COUNT issues with status `READY FOR QA` or `IN TESTING` |
| **Tested** | COUNT issues with status `AWAITING DEPLOYMENT` or `DONE` |

### Manually Entered by User (per board)

Prompts include the AICW name so the user knows which board is being asked about.

| Field | Prompt Text | Required? |
|---|---|---|
| **Test Cases Executed** | `How many test cases did you execute for {AICW} this week?` | Yes |
| **Test Cases Outstanding** | `How many test cases are still outstanding for {AICW}?` | Yes |
| **Defects Raised** | `How many defects did you raise for {AICW}? (tickets sent to dev after testing)` | Yes |
| **Blockers / Notes** | `Any blockers, risks, or key updates for {AICW}? (press Enter to skip)` | No |

### Manually Entered by User (once, after all boards)

| Field | Prompt Text | Required? |
|---|---|---|
| **UAT applicable?** | `Is UAT applicable this week? (y/n)` | Yes |
| — if UAT = yes — | | |
| **UAT Start Date** | `UAT Start Date (e.g. 14 Apr 2026):` | Yes |
| **UAT End Date** | `UAT End Date (e.g. 18 Apr 2026):` | Yes |
| **Test Cases Preparation Status** | `Test cases preparation status (e.g. 80% complete):` | Yes |
| **Requirements Modified** | `Requirements Modified? (Y/N):` | Yes |
| **Confluence Updated** | `Confluence Updated? (Y/N):` | Yes |
| **Confluence Link** | `Confluence link (press Enter to skip):` | No |

---

## Output Format

A **single combined email** is printed covering all boards. Each board has its own section. UAT appears once at the bottom.

```
Subject: Weekly QA Status Update – {{ Team Member }} – {{ Reporting Week }}

Hi Team,

Please find my weekly QA status update below.

---

Weekly QA Status

Team Member:    {{ Team Member }}
Reporting Week: {{ Reporting Week }}

[ {{ AICW / Customer — Board 1 }} ]

AICW / Customer: {{ AICW / Customer — Board 1 }}
Sprint:          {{ Sprint — Board 1 }}

QA Metrics for Current Sprint

  • Total Tickets:                              {{ Total Tickets }}
  • Pending Testing:                            {{ Pending Testing }}
  • Tested:                                     {{ Tested }}
  • Test Cases Executed:                        {{ Test Cases Executed }}
  • Test Cases Outstanding:                     {{ Test Cases Outstanding }}
  • Defects raised (tickets sent to dev):       {{ Defects Raised }}

Blockers / Notes

  {{ Blockers / Notes | default: "No blockers this week." }}

[ {{ AICW / Customer — Board 2 }} ]

  ... (same structure per board) ...

UAT Status{{ " – N/A" if UAT not applicable }}

  • UAT Start Date:                {{ UAT Start Date }}
  • UAT End Date:                  {{ UAT End Date }}
  • Test Cases Preparation Status: {{ Test Cases Preparation Status }}
  • Requirements Modified:         {{ Requirements Modified }}
  • Confluence Updated:            {{ Confluence Updated }}{{ " – " + link if provided }}

---

Thank you,
{{ Team Member }}
```

---

## Script Behaviour

1. On launch, derive reporting week from current date
2. For each board:
   a. Fetch sprint data (MCP → REST API → manual fallback)
   b. If board has `zephyr_project_key`: fetch sprint-scoped test case stats from Zephyr Scale
   c. Display sprint preview (sprint name, AICW, ticket counts; Zephyr pre-fills if available)
   d. Prompt user for per-board manual fields — executed/outstanding show `[N]` defaults from Zephyr; press Enter to accept or type to override
3. After all boards, prompt for UAT fields once
4. If UAT = `n`, show `UAT Status – N/A` in the email
5. Print a single combined formatted email to terminal
6. Open Microsoft Outlook with a new compose window pre-filled with the subject and formatted HTML body
7. If Outlook is unavailable: print `✅ Email ready — copy the above and paste into Outlook.`

---

## Error Handling

- If Jira MCP connection fails → fall back to REST API automatically
- If Jira REST API fails → print troubleshooting instructions, then prompt user to enter all fields manually
- If active sprint cannot be found → raise error with board details
- If epic names cannot be matched → warn user, list available epics, count all sprint tickets as fallback
- If Zephyr Scale API fails or key is missing → skip pre-fills, prompt manually (no crash)
- If Outlook AppleScript fails → fall back to plain-text copy prompt (no crash)

---

## File Structure

```
qa-report/
├── qa_report.py                  # Main script
├── .env                          # Env vars (gitignored)
├── .env.example                  # Template for env vars
├── requirements.txt              # Python dependencies (install via conda)
├── README.md                     # Setup and usage guide
├── CLAUDE.md                     # Guidance for Claude Code
└── QA_REPORT_REQUIREMENTS.md    # This file
```

---

## Dependencies (requirements.txt)

```
anthropic
requests
python-dotenv
```

Install via conda (PyPI is blocked on this machine):

```bash
conda install -c conda-forge anthropic requests python-dotenv
```
