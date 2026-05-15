# QA Weekly Status Report Generator

A Python script that automatically generates a weekly QA status email by pulling sprint data from Jira and prompting for manual fields. After the last prompt, it opens Microsoft Outlook with a new compose window pre-filled with the formatted email — no copy-pasting required.

## Features

- Pulls sprint data (sprint name, ticket counts, team member) from Jira via REST API
- Supports multiple Jira boards / instances in one run
- Detects active use cases (epics) from sprint issues and the sprint name
- **Zephyr Scale integration** — auto-fetches sprint-scoped test case execution stats (executed / outstanding) and pre-fills the prompts; Xander filters to its folder subtree, Dixie uses issue-link matching
- Prompts for per-use-case metrics (test cases, defects, blockers)
- Asks UAT questions once at the end and appends a shared UAT section
- **Auto-opens Microsoft Outlook** with a properly formatted HTML draft — subject, body, and all metrics pre-filled

## Prerequisites

- Python via [conda](https://docs.conda.io/) (PyPI may be blocked on corporate networks)
- Atlassian account with API token access

## Setup

### 1. Install dependencies

```bash
conda install -c conda-forge anthropic requests python-dotenv
```

### 2. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in:

| Variable | Description |
|---|---|
| `ATLASSIAN_EMAIL` | Your Atlassian account email |
| `ATLASSIAN_API_TOKEN` | API token from [id.atlassian.com](https://id.atlassian.com/manage-profile/security/api-tokens) |
| `ZEPHYR_SCALE_API_KEY` | Zephyr Scale API key (see [Zephyr Scale setup](#zephyr-scale-setup)) |
| `ANTHROPIC_API_KEY` | *(Optional)* Only needed for the Atlassian MCP path |

## Usage

```bash
conda activate base
python qa_report.py
```

The script will:

1. Fetch sprint data for each configured board
2. Show a preview (sprint name, AICW/Customer, ticket counts)
3. Fetch Zephyr Scale test case stats for each board and pre-fill executed/outstanding prompts
4. Prompt for per-board manual fields (press Enter to accept Zephyr pre-fills)
5. Ask UAT questions once at the end
6. Print the plain-text email to the terminal
7. **Automatically open Microsoft Outlook** with a formatted HTML draft ready to send

## Output

```
Subject: Weekly QA Status Update – Andy Peng – 13 Apr – 17 Apr 2026

Hi Team,

Please find my weekly QA status update below.

---

Weekly QA Status

Team Member:    Andy Peng
Reporting Week: 13 Apr – 17 Apr 2026

[ Dixie – Healius ]

AICW / Customer: Dixie – Healius
Sprint:          HEAL Dixie Sprint 5

QA Metrics for Current Sprint

  • Total Tickets:                              8
  • Pending Testing:                            3
  • Tested:                                     4
  • Test Cases Executed:                        6
  • Test Cases Outstanding:                     2
  • Defects raised (tickets sent to dev):       1

Blockers / Notes

  No blockers this week.

[ Xander – Healius ]

  ...

UAT Status – N/A

---

Thank you,
Andy Peng
```

## Board Configuration

Boards are defined in the `BOARDS` list at the top of `qa_report.py`:

```python
BOARDS = [
    {
        "label":             "Dixie / HEAL",
        "base_url":          "https://futuresecureai.atlassian.net",
        "board_id":          "3566",
        "target_epics":      ["Dixie"],
        "customer":          "Healius",
        "zephyr_project_key": "HEAL",   # enables Zephyr Scale auto-fetch
    },
    {
        "label":             "Xander / HEAL",
        "base_url":          "https://futuresecureai.atlassian.net",
        "board_id":          "3426",
        "target_epics":      ["Xander"],
        "customer":          "Healius",
        "zephyr_project_key": "HEAL",   # enables Zephyr Scale auto-fetch
        "zephyr_folder":     "Xander AI",  # restricts fetch to this folder subtree
    },
]
```

To add a board, append a new entry with `base_url`, `board_id`, `target_epics`, and `customer`. Add `zephyr_project_key` to enable Zephyr auto-fetch. Add `zephyr_folder` to restrict to a specific folder subtree (partial, case-insensitive match against root-level folders).

## Zephyr Scale Setup

Both boards have Zephyr Scale (SmartBear) installed. The script automatically fetches how many test cases linked to sprint issues are executed (Pass/Fail/Blocked) vs outstanding (Unexecuted/other).

Because the Zephyr `/v2/testcases` API ignores `issueKey` and `folderId` query params, the script fetches all project TCs and filters client-side using each TC's `links.issues[].issueId` (matched against integer Jira issue IDs) and `folder.id` (matched against the configured folder subtree via BFS on `parentId`).

**To generate the API key:**

1. Go to `https://futuresecureai.atlassian.net`
2. Top nav → **Apps** → **Zephyr Scale**
3. Settings/gear icon → **API Access Tokens**
4. Generate a new token and copy it
5. Add to `.env`: `ZEPHYR_SCALE_API_KEY=<token>`

If the key is missing or the API call fails, the script falls back to manual entry without pre-fills.

## Jira Status Mappings

| Jira Status | Report Field |
|---|---|
| `READY FOR QA`, `IN TESTING` | Pending Testing |
| `AWAITING DEPLOYMENT`, `DONE` | Tested |
| `TO DO`, `IN PROGRESS`, `PEER REVIEW`, `BLOCKED` | Not counted |

## Data Fetch Strategy

For each board, the script tries in order:

1. **Atlassian MCP** via Anthropic SDK *(requires `ANTHROPIC_API_KEY`)*
2. **Jira REST API** via Basic Auth *(default path)*
3. **Manual prompts** if both API paths fail

## Outlook Draft

After answering all prompts the script automatically opens Microsoft Outlook (macOS) with a new compose window. The draft includes:

- Subject line pre-filled
- Properly formatted HTML body (bold headers, grey section dividers, bullet lists)
- Both boards' metrics in separate sections
- UAT block at the bottom

If Outlook cannot be opened via AppleScript, the plain-text email printed to the terminal can still be copied and pasted manually.

## File Structure

```
qa-weekly-report/
├── qa_report.py                 # Main script
├── .env                         # Your credentials (gitignored)
├── .env.example                 # Credential template
├── requirements.txt             # Dependencies
├── QA_REPORT_REQUIREMENTS.md   # Detailed requirements spec
└── CLAUDE.md                    # Guidance for Claude Code
```
