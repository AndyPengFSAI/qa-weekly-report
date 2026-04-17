#!/usr/bin/env python3
"""
QA Weekly Status Report Generator

Fetches sprint data from Jira (REST API) for each configured board,
prompts the user for manual fields, and prints copy-paste-ready emails.
"""

import os
import base64
import json
import re
import subprocess
import tempfile
from datetime import datetime, timedelta

import anthropic
import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration — one entry per Jira board
# ---------------------------------------------------------------------------

BOARDS = [
    {
        "label":        "Healius / FDW  (Reva · Melinda · Dixie)",
        "base_url":     "https://healius-digital.atlassian.net",
        "board_id":     "402",
        "target_epics": ["Reva", "Melinda", "Dixie"],
        "customer":     "Healius",
    },
    {
        "label":             "Xander / HEAL",
        "base_url":          "https://futuresecureai.atlassian.net",
        "board_id":          "3426",
        "target_epics":      ["Xander"],
        "customer":          "Healius",
        "zephyr_project_key": "HEAL",
    },
]

PENDING_STATUSES = {"READY FOR QA", "IN TESTING"}
TESTED_STATUSES  = {"AWAITING DEPLOYMENT", "DONE"}

ZEPHYR_BASE             = "https://api.zephyrscale.smartbear.com/v2"
ZEPHYR_EXECUTED_STATUSES = {"pass", "fail", "blocked"}  # lowercase for case-insensitive compare


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_reporting_week() -> str:
    """Return 'D Mon – D Mon YYYY' for the current Mon–Fri week."""
    today  = datetime.now()
    monday = today - timedelta(days=today.weekday())
    friday = monday + timedelta(days=4)
    return f"{monday.strftime('%-d %b')} – {friday.strftime('%-d %b %Y')}"


def _jira_auth_headers() -> dict:
    """Basic-Auth headers for the Jira REST API."""
    email = os.environ.get("ATLASSIAN_EMAIL", "")
    token = os.environ.get("ATLASSIAN_API_TOKEN", "")
    if not email or not token:
        raise EnvironmentError(
            "ATLASSIAN_EMAIL and ATLASSIAN_API_TOKEN must be set in .env"
        )
    creds = base64.b64encode(f"{email}:{token}".encode()).decode()
    return {"Authorization": f"Basic {creds}", "Accept": "application/json"}


def _zephyr_headers() -> dict:
    """Bearer-Auth headers for the Zephyr Scale REST API."""
    token = os.environ.get("ZEPHYR_SCALE_API_KEY", "")
    if not token:
        raise EnvironmentError("ZEPHYR_SCALE_API_KEY must be set in .env")
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


# ---------------------------------------------------------------------------
# Data fetching — primary: Atlassian MCP via Anthropic SDK
# ---------------------------------------------------------------------------

def fetch_via_mcp(board: dict) -> dict:
    """
    Use claude-sonnet-4-6 + the Atlassian remote MCP to pull sprint data.
    Requires ATLASSIAN_API_TOKEN to be a valid OAuth 2.0 token (not an API token).
    """
    api_key         = os.environ.get("ANTHROPIC_API_KEY", "")
    atlassian_token = os.environ.get("ATLASSIAN_API_TOKEN", "")

    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY is not set.")
    if not atlassian_token:
        raise EnvironmentError("ATLASSIAN_API_TOKEN is not set.")

    client = anthropic.Anthropic(api_key=api_key)
    target_epics = board["target_epics"]

    prompt = (
        f"You are fetching Jira data for a QA weekly report.\n\n"
        f"Jira base URL: {board['base_url']}\n"
        f"Board ID: {board['board_id']}\n\n"
        f"Please:\n"
        f"1. Get the authenticated user's display name.\n"
        f"2. Find the active sprint for board {board['board_id']} and note its name.\n"
        f"3. Count ALL issues in that sprint whose epic name contains one of: "
        f"{', '.join(target_epics)}.\n"
        f"4. Among those, count ones with status READY FOR QA or IN TESTING (→ pending).\n"
        f"5. Among those, count ones with status AWAITING DEPLOYMENT or DONE (→ tested).\n"
        f"6. List the distinct epic names found (comma-separated).\n\n"
        f"Respond with ONLY a single-line JSON — no explanation:\n"
        f'{{"team_member":"...","sprint":"...","aicw_customer":"...","total":N,"pending":N,"tested":N}}'
    )

    response = client.beta.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        betas=["mcp-client-2025-04-04"],
        mcp_servers=[
            {
                "type":                "url",
                "url":                 "https://mcp.atlassian.com/v1/mcp",
                "name":                "atlassian",
                "authorization_token": atlassian_token,
            }
        ],
        messages=[{"role": "user", "content": prompt}],
    )

    content = "".join(
        block.text for block in response.content if hasattr(block, "text")
    )
    match = re.search(r'\{[^{}]*"team_member"[^{}]*\}', content, re.DOTALL)
    if match:
        return json.loads(match.group())

    raise ValueError(
        f"Could not parse JSON from MCP response.\nRaw (500 chars):\n{content[:500]}"
    )


# ---------------------------------------------------------------------------
# Data fetching — fallback: direct Jira REST API
# ---------------------------------------------------------------------------

def _paginated_jql(jql: str, headers: dict, base_url: str,
                   fields: str = "status") -> list:
    """Return all issues for a JQL query, handling pagination."""
    issues   = []
    start_at = 0
    page_size = 100
    while True:
        r = requests.get(
            f"{base_url}/rest/api/3/search/jql",
            params={
                "jql":        jql,
                "startAt":    start_at,
                "maxResults": page_size,
                "fields":     fields,
            },
            headers=headers,
            timeout=20,
        )
        if not r.ok:
            try:
                errors = r.json().get("errorMessages") or [r.text]
            except Exception:
                errors = [r.text]
            raise requests.HTTPError(
                f"JQL query failed ({r.status_code}): {errors}\nJQL: {jql}"
            )
        body     = r.json()
        page     = body.get("issues", [])
        issues  += page
        total    = body.get("total", 0)
        start_at += len(page)
        if start_at >= total or not page:
            break
    return issues


def fetch_via_rest(board: dict) -> dict:
    """Fetch sprint data directly via the Jira REST API (Basic Auth)."""
    headers  = _jira_auth_headers()
    base_url = board["base_url"]
    board_id = board["board_id"]
    target_epics = board["target_epics"]

    # 1. Authenticated user
    r = requests.get(f"{base_url}/rest/api/3/myself", headers=headers, timeout=15)
    r.raise_for_status()
    team_member = r.json().get("displayName", "Unknown")

    # 2. Active sprint
    r = requests.get(
        f"{base_url}/rest/agile/1.0/board/{board_id}/sprint",
        params={"state": "active"},
        headers=headers,
        timeout=15,
    )
    r.raise_for_status()
    sprints = r.json().get("values", [])
    if not sprints:
        raise ValueError(f"No active sprint found for board {board_id}.")
    sprint_id   = sprints[0]["id"]
    sprint_name = sprints[0]["name"]

    # 3. Fetch ALL sprint issues with epic-detection fields.
    #    No project filter — board is multi-project.
    #    Next-gen Jira: epic = parent field (issuetype = Epic)
    #    Classic Jira:  epic = customfield_10014 (Epic Link key)
    all_issues = _paginated_jql(
        jql=f"sprint = {sprint_id} ORDER BY created ASC",
        headers=headers,
        base_url=base_url,
        fields="status,summary,issuetype,parent,customfield_10014",
    )

    # 4. Build epic-key → name map for Classic projects.
    classic_epic_keys = {
        issue["fields"]["customfield_10014"]
        for issue in all_issues
        if issue["fields"].get("customfield_10014")
        and isinstance(issue["fields"]["customfield_10014"], str)
    }
    epic_name_map: dict[str, str] = {}
    if classic_epic_keys:
        epic_issues = _paginated_jql(
            jql=f"issue in ({', '.join(classic_epic_keys)})",
            headers=headers,
            base_url=base_url,
            fields="summary",
        )
        for e in epic_issues:
            epic_name_map[e["key"]] = e["fields"].get("summary", "")

    # 5. Filter by target epics; track matched epic names for AICW/Customer.
    filtered: list[dict]      = []
    found_epic_names: set[str] = set()
    matched_epic_names: set[str] = set()

    for issue in all_issues:
        fields     = issue["fields"]
        issue_type = fields.get("issuetype", {}).get("name", "")
        if issue_type == "Epic":
            continue  # don't count epics themselves

        epic_name: str | None = None

        # Next-gen: parent whose issuetype is Epic
        parent = fields.get("parent") or {}
        if parent:
            pf = parent.get("fields") or {}
            if pf.get("issuetype", {}).get("name") == "Epic":
                epic_name = pf.get("summary", "")

        # Classic: Epic Link key → resolved name
        if not epic_name:
            epic_key = fields.get("customfield_10014")
            if epic_key and isinstance(epic_key, str):
                epic_name = epic_name_map.get(epic_key, epic_key)

        if epic_name:
            found_epic_names.add(epic_name)
            if any(t.lower() in epic_name.lower() for t in target_epics):
                matched_epic_names.add(epic_name)
                filtered.append(issue)

    if not filtered:
        print(f"\n  ⚠️  No issues matched epics {target_epics}.")
        if found_epic_names:
            print("     Epic names found in this sprint:")
            for name in sorted(found_epic_names)[:30]:
                print(f"       {name}")
        else:
            print("     No epic data found on any sprint issue.")
        print("     Counting ALL sprint tickets (no epic filter).")
        filtered = [i for i in all_issues
                    if i["fields"].get("issuetype", {}).get("name") != "Epic"]

    # Build AICW: keywords from target_epics that appear in matched epic names
    # OR in the sprint name (e.g. "FDW UC 1.2 Melinda Sprint 11" → Melinda).
    # Preserve order from target_epics; join with "/".
    aicw_keywords: list[str] = []
    seen_kw: set[str] = set()
    for kw in target_epics:
        kw_lower = kw.lower()
        matched_via_epic = any(kw_lower in n.lower() for n in matched_epic_names)
        matched_via_sprint = kw_lower in sprint_name.lower()
        if (matched_via_epic or matched_via_sprint) and kw not in seen_kw:
            aicw_keywords.append(kw)
            seen_kw.add(kw)

    aicw          = "/".join(aicw_keywords) if aicw_keywords else "/".join(target_epics)
    aicw_customer = f"{aicw} – {board['customer']}"

    total_count   = len(filtered)
    pending_count = sum(
        1 for i in filtered
        if i["fields"]["status"]["name"].upper() in PENDING_STATUSES
    )
    tested_count = sum(
        1 for i in filtered
        if i["fields"]["status"]["name"].upper() in TESTED_STATUSES
    )

    # Zephyr Scale test case stats — sprint-scoped via issue links
    zephyr_stats: dict | None = None
    if board.get("zephyr_project_key"):
        try:
            sprint_issue_keys = {issue["key"] for issue in filtered}
            zephyr_stats = fetch_zephyr_test_stats(
                board["zephyr_project_key"], sprint_issue_keys
            )
        except Exception as z_err:
            print(f"  ⚠ Zephyr fetch failed ({z_err})")

    return {
        "team_member":   team_member,
        "sprint":        sprint_name,
        "aicw_customer": aicw_customer,
        "total":         total_count,
        "pending":       pending_count,
        "tested":        tested_count,
        "zephyr_stats":  zephyr_stats,
    }


# ---------------------------------------------------------------------------
# Data fetching — Zephyr Scale (sprint-scoped test case stats)
# ---------------------------------------------------------------------------

def fetch_zephyr_test_stats(project_key: str, sprint_issue_keys: set) -> dict:
    """
    Fetch test case execution stats from Zephyr Scale, scoped to the current sprint.

    Only counts test cases that are linked to at least one Jira issue in
    sprint_issue_keys.  Returns executed / outstanding based on latest execution
    status per test case.
    """
    headers = _zephyr_headers()

    # 1. For each sprint issue key, fetch linked test cases via the issueKey filter.
    #    The Zephyr Scale API supports GET /testcases?issueKey={key} which returns
    #    all test cases with a COVERAGE link to that Jira issue.
    sprint_tc_keys: list[str] = []
    seen_tc_keys:   set[str]  = set()
    page_size = 100

    for issue_key in sprint_issue_keys:
        start_at = 0
        while True:
            r = requests.get(
                f"{ZEPHYR_BASE}/testcases",
                params={
                    "projectKey": project_key,
                    "issueKey":   issue_key,
                    "maxResults":  page_size,
                    "startAt":     start_at,
                },
                headers=headers,
                timeout=20,
            )
            r.raise_for_status()
            body  = r.json()
            page  = body.get("values", [])
            total = body.get("total", 0)
            for tc in page:
                tc_key = tc.get("key", "")
                if tc_key and tc_key not in seen_tc_keys:
                    sprint_tc_keys.append(tc_key)
                    seen_tc_keys.add(tc_key)
            start_at += len(page)
            if start_at >= total or not page:
                break

    if not sprint_tc_keys:
        return {"executed": 0, "outstanding": 0, "total": 0}

    sprint_tc_set = set(sprint_tc_keys)

    # 2. Collect ALL test executions in the project; keep latest per test case key.
    latest_status: dict[str, str] = {}   # tc_key → status name
    latest_date:   dict[str, str] = {}   # tc_key → actualEndDate string (for tie-breaking)
    start_at = 0
    while True:
        r = requests.get(
            f"{ZEPHYR_BASE}/testexecutions",
            params={
                "projectKey": project_key,
                "maxResults":  page_size,
                "startAt":     start_at,
            },
            headers=headers,
            timeout=20,
        )
        r.raise_for_status()
        body  = r.json()
        page  = body.get("values", [])
        total = body.get("total", 0)
        for ex in page:
            tc_key = (ex.get("testCase") or {}).get("key", "")
            if tc_key not in sprint_tc_set:
                continue
            status = (ex.get("status") or {}).get("name", "")
            date   = ex.get("actualEndDate") or ""
            # Keep the most recent execution (higher date string wins; equal → last record)
            if tc_key not in latest_date or date >= latest_date[tc_key]:
                latest_status[tc_key] = status
                latest_date[tc_key]   = date
        start_at += len(page)
        if start_at >= total or not page:
            break

    # 3. Count executed vs outstanding.
    executed    = 0
    outstanding = 0
    for tc_key in sprint_tc_keys:
        status = latest_status.get(tc_key, "")
        if status.lower() in ZEPHYR_EXECUTED_STATUSES:
            executed += 1
        else:
            outstanding += 1

    return {"executed": executed, "outstanding": outstanding, "total": len(sprint_tc_keys)}


# ---------------------------------------------------------------------------
# Interactive prompts
# ---------------------------------------------------------------------------

def _ask(prompt_text: str, required: bool = True) -> str:
    while True:
        value = input(f"  {prompt_text} ").strip()
        if value or not required:
            return value
        print("    (Required — please enter a value.)")


def _ask_with_default(prompt_text: str, default: str | None = None) -> str:
    """Like _ask but shows a pre-filled default; Enter accepts it."""
    suffix = f" [{default}]" if default is not None else ""
    while True:
        value = input(f"  {prompt_text}{suffix} ").strip()
        if value:
            return value
        if default is not None:
            return default
        print("    (Required — please enter a value.)")


def _ask_yn(prompt_text: str) -> bool:
    while True:
        answer = input(f"  {prompt_text} ").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("    (Please enter y or n.)")


def prompt_auto_fields(board: dict) -> dict:
    """Fallback: prompt the user for Jira-derived fields when the API is down."""
    print("\n  Enter sprint / ticket data manually:\n")
    return {
        "team_member":   _ask("Your full name (Team Member):"),
        "sprint":        _ask("Sprint name:"),
        "aicw_customer": _ask(
            f"AICW (use case / epic names, e.g. {'/'.join(board['target_epics'])}) – {board['customer']}:"
        ),
        "total":         _ask("Total tickets (matching epics):"),
        "pending":       _ask("Pending Testing (READY FOR QA + IN TESTING):"),
        "tested":        _ask("Tested (AWAITING DEPLOYMENT + DONE):"),
    }


def prompt_board_fields(aicw: str, zephyr_stats: dict | None = None) -> dict:
    """Prompt for per-board manual fields (test cases, defects, blockers).

    If zephyr_stats is provided, pre-fills executed/outstanding from Zephyr Scale
    and lets the user press Enter to accept or type a number to override.
    """
    print()
    if zephyr_stats:
        print(
            f"  Zephyr Scale (sprint-scoped): "
            f"{zephyr_stats['executed']} executed, "
            f"{zephyr_stats['outstanding']} outstanding "
            f"({zephyr_stats['total']} test cases linked to sprint tickets)\n"
            f"  Press Enter to accept, or type a number to override."
        )
    exe_default = str(zephyr_stats["executed"])    if zephyr_stats else None
    out_default = str(zephyr_stats["outstanding"]) if zephyr_stats else None

    test_cases_executed    = _ask_with_default(
        f"How many test cases did you execute for {aicw} this week?", exe_default
    )
    test_cases_outstanding = _ask_with_default(
        f"How many test cases are still outstanding for {aicw}?", out_default
    )
    defects_raised         = _ask(
        f"How many defects did you raise for {aicw}? (tickets sent to dev after testing)"
    )
    blockers = _ask(
        f"Any blockers, risks, or key updates for {aicw}? (press Enter to skip)",
        required=False,
    )
    return {
        "test_cases_executed":    test_cases_executed,
        "test_cases_outstanding": test_cases_outstanding,
        "defects_raised":         defects_raised,
        "blockers":               blockers or "No blockers this week.",
    }


def prompt_uat_fields() -> dict:
    """Prompt for UAT fields — asked once after all boards."""
    uat_applicable = _ask_yn("Is UAT applicable this week? (y/n)")
    uat_data: dict = {"uat_applicable": uat_applicable}
    if uat_applicable:
        print()
        uat_data["start_date"]            = _ask("UAT Start Date (e.g. 14 Apr 2026):")
        uat_data["end_date"]              = _ask("UAT End Date (e.g. 18 Apr 2026):")
        uat_data["prep_status"]           = _ask("Test cases preparation status (e.g. 80% complete):")
        uat_data["requirements_modified"] = "Yes" if _ask_yn("Requirements Modified? (Y/N):") else "No"
        uat_data["confluence_updated"]    = "Yes" if _ask_yn("Confluence Updated? (Y/N):")    else "No"
        uat_data["confluence_link"]       = _ask(
            "Confluence link (press Enter to skip):", required=False
        )
    return uat_data


# ---------------------------------------------------------------------------
# Email builder
# ---------------------------------------------------------------------------

def build_combined_email(
    boards_data: list,
    uat: dict,
    reporting_week: str,
) -> str:
    """Build a single combined email covering all boards plus one UAT section."""
    team_member = boards_data[0][0].get("team_member", "Unknown")

    board_sections: list[str] = []
    for auto, manual in boards_data:
        aicw_customer = auto.get("aicw_customer", "N/A")
        section = (
            f"[ {aicw_customer} ]\n"
            f"\n"
            f"AICW / Customer: {aicw_customer}\n"
            f"Sprint:          {auto.get('sprint', 'Unknown')}\n"
            f"\n"
            f"QA Metrics for Current Sprint\n"
            f"\n"
            f"  • Total Tickets:                              {auto.get('total', 0)}\n"
            f"  • Pending Testing:                            {auto.get('pending', 0)}\n"
            f"  • Tested:                                     {auto.get('tested', 0)}\n"
            f"  • Test Cases Executed:                        {manual['test_cases_executed']}\n"
            f"  • Test Cases Outstanding:                     {manual['test_cases_outstanding']}\n"
            f"  • Defects raised (tickets sent to dev):       {manual['defects_raised']}\n"
            f"\n"
            f"Blockers / Notes\n"
            f"\n"
            f"  {manual['blockers']}"
        )
        board_sections.append(section)

    if uat.get("uat_applicable"):
        confluence_value = uat.get("confluence_updated", "N/A")
        if uat.get("confluence_link"):
            confluence_value += f" – {uat['confluence_link']}"
        uat_block = (
            f"UAT Status\n\n"
            f"  • UAT Start Date:                {uat.get('start_date', 'N/A')}\n"
            f"  • UAT End Date:                  {uat.get('end_date', 'N/A')}\n"
            f"  • Test Cases Preparation Status: {uat.get('prep_status', 'N/A')}\n"
            f"  • Requirements Modified:         {uat.get('requirements_modified', 'N/A')}\n"
            f"  • Confluence Updated:            {confluence_value}"
        )
    else:
        uat_block = "UAT Status – N/A"

    boards_body = "\n\n".join(board_sections)

    return (
        f"Subject: Weekly QA Status Update – {team_member} – {reporting_week}\n"
        f"\n"
        f"Hi Team,\n"
        f"\n"
        f"Please find my weekly QA status update below.\n"
        f"\n"
        f"---\n"
        f"\n"
        f"Weekly QA Status\n"
        f"\n"
        f"Team Member:    {team_member}\n"
        f"Reporting Week: {reporting_week}\n"
        f"\n"
        f"{boards_body}\n"
        f"\n"
        f"{uat_block}\n"
        f"\n"
        f"---\n"
        f"\n"
        f"Thank you,\n"
        f"{team_member}"
    )


# ---------------------------------------------------------------------------
# HTML email builder + Outlook launcher
# ---------------------------------------------------------------------------

def build_html_email(
    boards_data: list,
    uat: dict,
    reporting_week: str,
) -> str:
    """Build an HTML email body suitable for Outlook's compose window."""
    team_member = boards_data[0][0].get("team_member", "Unknown")

    def kv_row(label: str, value: str) -> str:
        return (
            f'<tr>'
            f'<td style="padding:2px 16px 2px 0;white-space:nowrap;vertical-align:top">'
            f'<b>{label}</b></td>'
            f'<td style="padding:2px 0;vertical-align:top">{value}</td>'
            f'</tr>'
        )

    board_html_parts: list[str] = []
    for auto, manual in boards_data:
        aicw   = auto.get("aicw_customer", "N/A")
        sprint = auto.get("sprint", "Unknown")
        part = (
            f'<p style="margin:16px 0 4px 0;padding:6px 10px;background:#f0f0f0">'
            f'<b>[ {aicw} ]</b></p>'
            f'<table style="border-collapse:collapse;margin-bottom:10px">'
            f'{kv_row("AICW / Customer:", aicw)}'
            f'{kv_row("Sprint:", sprint)}'
            f'</table>'
            f'<p style="margin:10px 0 4px 0"><b>QA Metrics for Current Sprint</b></p>'
            f'<ul style="margin:0 0 10px 16px;padding:0;line-height:1.8">'
            f'<li>Total Tickets: {auto.get("total", 0)}</li>'
            f'<li>Pending Testing: {auto.get("pending", 0)}</li>'
            f'<li>Tested: {auto.get("tested", 0)}</li>'
            f'<li>Test Cases Executed: {manual["test_cases_executed"]}</li>'
            f'<li>Test Cases Outstanding: {manual["test_cases_outstanding"]}</li>'
            f'<li>Defects raised (tickets sent to dev): {manual["defects_raised"]}</li>'
            f'</ul>'
            f'<p style="margin:10px 0 4px 0"><b>Blockers / Notes</b></p>'
            f'<p style="margin:0 0 6px 0">{manual["blockers"]}</p>'
        )
        board_html_parts.append(part)

    if uat.get("uat_applicable"):
        conf = uat.get("confluence_updated", "N/A")
        if uat.get("confluence_link"):
            link = uat["confluence_link"]
            conf += f' – <a href="{link}">{link}</a>'
        uat_html = (
            f'<p style="margin:16px 0 4px 0"><b>UAT Status</b></p>'
            f'<ul style="margin:0 0 10px 16px;padding:0;line-height:1.8">'
            f'<li>UAT Start Date: {uat.get("start_date", "N/A")}</li>'
            f'<li>UAT End Date: {uat.get("end_date", "N/A")}</li>'
            f'<li>Test Cases Preparation Status: {uat.get("prep_status", "N/A")}</li>'
            f'<li>Requirements Modified: {uat.get("requirements_modified", "N/A")}</li>'
            f'<li>Confluence Updated: {conf}</li>'
            f'</ul>'
        )
    else:
        uat_html = '<p style="margin:16px 0 4px 0"><b>UAT Status – N/A</b></p>'

    hr = '<hr style="border:none;border-top:1px solid #cccccc;margin:14px 0">'
    boards_body = "\n".join(board_html_parts)

    return (
        '<html><head><meta charset="utf-8"></head>\n'
        '<body style="font-family:Arial,sans-serif;font-size:11pt;color:#222222;'
        'margin:0;padding:16px;max-width:680px">\n'
        '<p style="margin:0 0 10px 0">Hi Team,</p>\n'
        '<p style="margin:0 0 10px 0">Please find my weekly QA status update below.</p>\n'
        f'{hr}\n'
        f'<p style="margin:0 0 8px 0"><b>Weekly QA Status</b></p>\n'
        f'<table style="border-collapse:collapse;margin-bottom:12px">\n'
        f'{kv_row("Team Member:", team_member)}\n'
        f'{kv_row("Reporting Week:", reporting_week)}\n'
        f'</table>\n'
        f'{boards_body}\n'
        f'{uat_html}\n'
        f'{hr}\n'
        f'<p style="margin:0">Thank you,<br>{team_member}</p>\n'
        f'</body></html>'
    )


def _open_outlook_draft(subject: str, html_body: str) -> bool:
    """
    Open Microsoft Outlook with a new compose window pre-filled with the given
    subject and HTML body.  Uses AppleScript (macOS only).
    Returns True on success, False if Outlook is unavailable or the script fails.
    """
    # Write HTML to a temp file — avoids embedding a large string in AppleScript
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", delete=False, encoding="utf-8"
    ) as fh:
        fh.write(html_body)
        html_path = fh.name

    subject_safe = subject.replace("\\", "\\\\").replace('"', '\\"')
    script = (
        f'set htmlPath to POSIX file "{html_path}"\n'
        f'set htmlContent to read htmlPath as «class utf8»\n'
        f'tell application "Microsoft Outlook"\n'
        f'    activate\n'
        f'    set theMsg to make new outgoing message with properties '
        f'{{subject:"{subject_safe}", html content:htmlContent}}\n'
        f'    open theMsg\n'
        f'end tell\n'
    )

    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)

    try:
        os.unlink(html_path)
    except OSError:
        pass

    return result.returncode == 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _fetch_auto_data(board: dict) -> dict:
    """Try MCP → REST → manual fallback. Returns auto_data dict."""
    # Primary: Atlassian MCP
    try:
        data = fetch_via_mcp(board)
        print("  ✓ Data fetched via Atlassian MCP.")
        return data
    except Exception as mcp_err:
        print(f"  ✗ MCP unavailable ({mcp_err})")
        print("    Trying Jira REST API...")

    # Fallback: direct REST
    try:
        data = fetch_via_rest(board)
        print("  ✓ Data fetched via Jira REST API.")
        return data
    except Exception as rest_err:
        print(f"\n  ✗ Jira REST API failed: {rest_err}")
        print(
            "\n  Troubleshooting:\n"
            "    • Check ATLASSIAN_EMAIL and ATLASSIAN_API_TOKEN in .env\n"
            "    • Generate a token at: "
            "https://id.atlassian.com/manage-profile/security/api-tokens\n"
        )
        return prompt_auto_fields(board)


def main() -> None:
    reporting_week = get_reporting_week()
    boards_data: list = []

    print()
    print("=" * 60)
    print("   QA Weekly Status Report Generator")
    print("=" * 60)
    print(f"\n  Reporting Week: {reporting_week}")

    for i, board in enumerate(BOARDS, start=1):
        print()
        print(f"  [{i}/{len(BOARDS)}]  {board['label']}")
        print("  " + "-" * 56)
        print("  Fetching sprint data from Jira...")

        auto_data = _fetch_auto_data(board)

        print(f"\n  Sprint          : {auto_data['sprint']}")
        print(f"  AICW / Customer : {auto_data['aicw_customer']}")
        print(f"  Total: {auto_data['total']}  |  "
              f"Pending: {auto_data['pending']}  |  "
              f"Tested: {auto_data['tested']}")

        print()
        print("  Please answer the following questions:")
        print("  " + "-" * 56)

        board_manual = prompt_board_fields(
            auto_data.get("aicw_customer", board["label"]),
            zephyr_stats=auto_data.get("zephyr_stats"),
        )
        boards_data.append((auto_data, board_manual))

    # UAT — asked once after all boards
    print()
    print("  UAT")
    print("  " + "-" * 56)
    uat_data = prompt_uat_fields()

    # Print single combined email
    email = build_combined_email(boards_data, uat_data, reporting_week)
    print()
    print("=" * 60)
    print("  WEEKLY QA STATUS EMAIL")
    print("=" * 60)
    print()
    print(email)
    print()
    print("=" * 60)
    print()
    # Open Outlook with a pre-filled HTML draft
    team_member_name = boards_data[0][0].get("team_member", "")
    subject = f"Weekly QA Status Update – {team_member_name} – {reporting_week}"
    html = build_html_email(boards_data, uat_data, reporting_week)

    print("  Opening Outlook draft...")
    if _open_outlook_draft(subject, html):
        print("✅ Outlook draft opened — review and send.")
    else:
        print("✅ Email ready — copy the above and paste into Outlook.")
    print()


if __name__ == "__main__":
    main()
