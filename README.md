# QA Weekly Status Report Generator

A Python script that automatically generates a weekly QA status email by pulling sprint data from Jira and prompting for manual fields. Outputs a single formatted, copy-paste-ready email covering all active use cases.

## Features

- Pulls sprint data (sprint name, ticket counts, team member) from Jira via REST API
- Supports multiple Jira boards / instances in one run
- Detects active use cases (epics) from sprint issues and the sprint name
- Prompts for per-use-case metrics (test cases, defects, blockers)
- Asks UAT questions once at the end and appends a shared UAT section
- Outputs one combined email — ready to paste into Outlook

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
| `ANTHROPIC_API_KEY` | *(Optional)* Only needed for the Atlassian MCP path |

## Usage

```bash
conda activate base
python qa_report.py
```

The script will:

1. Fetch sprint data for each configured board
2. Show a preview (sprint name, AICW/Customer, ticket counts)
3. Prompt for per-board manual fields
4. Ask UAT questions once at the end
5. Print the combined email

## Output

```
Subject: Weekly QA Status Update – Andy Peng – 13 Apr – 17 Apr 2026

Hi Team,

Please find my weekly QA status update below.

---

Weekly QA Status

Team Member:    Andy Peng
Reporting Week: 13 Apr – 17 Apr 2026

[ Reva/Melinda – Healius ]

AICW / Customer: Reva/Melinda – Healius
Sprint:          FDW UC 1.2 Melinda Sprint 11

QA Metrics for Current Sprint

  • Total Tickets:                              8
  • Pending Testing:                            3
  • Tested:                                     4
  • Test Cases Executed:                        10
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
        "label":        "Healius / FDW  (Reva · Melinda · Dixie)",
        "base_url":     "https://healius-digital.atlassian.net",
        "board_id":     "402",
        "target_epics": ["Reva", "Melinda", "Dixie"],
        "customer":     "Healius",
    },
    {
        "label":        "Xander / HEAL",
        "base_url":     "https://futuresecureai.atlassian.net",
        "board_id":     "3426",
        "target_epics": ["Xander"],
        "customer":     "Healius",
    },
]
```

To add a board, append a new entry with the appropriate `base_url`, `board_id`, `target_epics`, and `customer`.

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
