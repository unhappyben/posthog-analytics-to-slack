#!/usr/bin/env python3
"""
PostHog → Slack Error Reporter
==============================
Checks for errors every 10 mins and posts to Slack.

Environment variables:
    POSTHOG_API_KEY       - Your PostHog personal API key
    POSTHOG_PROJECT_ID    - Your PostHog project ID
    SLACK_WEBHOOK_ERRORS  - Slack webhook for errors channel

Usage:
    python error_slack_reporter.py
    python error_slack_reporter.py --test
"""

import os
import requests
from datetime import datetime, timedelta

# =============================================================================
# CONFIG
# =============================================================================

POSTHOG_API_KEY = os.environ.get("POSTHOG_API_KEY")
POSTHOG_PROJECT_ID = os.environ.get("POSTHOG_PROJECT_ID")
POSTHOG_HOST = os.environ.get("POSTHOG_HOST", "https://app.posthog.com")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_ERRORS")

HEADERS = {
    "Authorization": f"Bearer {POSTHOG_API_KEY}",
    "Content-Type": "application/json"
}


def check_config():
    """Verify all required env vars are set."""
    missing = []
    if not POSTHOG_API_KEY:
        missing.append("POSTHOG_API_KEY")
    if not POSTHOG_PROJECT_ID:
        missing.append("POSTHOG_PROJECT_ID")
    if not SLACK_WEBHOOK_URL:
        missing.append("SLACK_WEBHOOK_ERRORS")
    
    if missing:
        print(f"❌ Missing environment variables: {', '.join(missing)}")
        exit(1)


# =============================================================================
# POSTHOG QUERIES
# =============================================================================

def query_posthog(query: str) -> dict:
    """Execute a HogQL query."""
    url = f"{POSTHOG_HOST}/api/projects/{POSTHOG_PROJECT_ID}/query/"
    payload = {"query": {"kind": "HogQLQuery", "query": query}}
    response = requests.post(url, headers=HEADERS, json=payload)
    
    if response.status_code >= 400:
        print(f"❌ PostHog error: {response.text}")
        return None
    return response.json()


def get_event_count_by_os(event_name: str, date_from: str, date_to: str) -> dict:
    """Get event counts by OS."""
    query = f"""
        SELECT properties.$os as os, count() as count
        FROM events
        WHERE event = '{event_name}'
            AND timestamp >= '{date_from}' AND timestamp < '{date_to}'
            AND (properties.$os = 'iOS' OR properties.$os = 'Android')
        GROUP BY os ORDER BY count DESC
    """
    result = query_posthog(query)
    counts = {"iOS": 0, "Android": 0}
    if result:
        for row in result.get("results", []):
            if row[0] in counts:
                counts[row[0]] = row[1]
    return counts


def get_error_counts(date_from: str, date_to: str) -> dict:
    """Get error counts by type and OS."""
    error_events = [
        "app_error_captured",
        "$exception",
        "buy_provider_availability_error",
        "send_transaction_result",
        "swap_execution_result",
        "$rageclick"
    ]
    
    errors = {}
    for event in error_events:
        counts = get_event_count_by_os(event, date_from, date_to)
        total = counts["iOS"] + counts["Android"]
        if total > 0:
            errors[event] = counts
    return errors


# =============================================================================
# SLACK
# =============================================================================

def send_slack(blocks: list, text: str):
    """Send to Slack."""
    response = requests.post(SLACK_WEBHOOK_URL, json={"text": text, "blocks": blocks})
    if response.status_code != 200:
        print(f"❌ Slack error: {response.text}")
        return False
    print("✅ Sent to Slack")
    return True


# =============================================================================
# ERROR REPORT
# =============================================================================

def check_errors():
    """Check for errors in last 10 mins and report."""
    check_config()
    
    now = datetime.utcnow()
    ten_mins_ago = now - timedelta(minutes=10)
    
    date_from = ten_mins_ago.strftime("%Y-%m-%d %H:%M:%S")
    date_to = now.strftime("%Y-%m-%d %H:%M:%S")
    
    print(f"🚨 Checking errors from {date_from} to {date_to}...")
    
    errors = get_error_counts(date_from, date_to)
    
    if not errors:
        print("✅ No errors in last 10 minutes")
        return
    
    # Build message
    error_lines = []
    total_errors = 0
    
    event_names = {
        "app_error_captured": "🐛 App Errors",
        "$exception": "💥 Exceptions",
        "buy_provider_availability_error": "💳 Payment Provider Errors",
        "send_transaction_result": "📤 Send Failures",
        "swap_execution_result": "🔄 Swap Failures",
        "$rageclick": "😤 Rage Clicks"
    }
    
    for event, counts in errors.items():
        total = counts["iOS"] + counts["Android"]
        total_errors += total
        name = event_names.get(event, event)
        error_lines.append(f"• {name}: iOS *{counts['iOS']}* · Android *{counts['Android']}*")
    
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"🚨 Error Alert — {total_errors} issues in last 10 min", "emoji": True}},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(error_lines)}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": 
            f"<https://app.posthog.com/project/{POSTHOG_PROJECT_ID}/dashboard/859640|View Error Dashboard> · {now.strftime('%H:%M UTC')}"
        }]}
    ]
    
    send_slack(blocks, f"🚨 {total_errors} errors in last 10 min")


def test_slack():
    """Test connection."""
    check_config()
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "✅ *Error reporter connected!*\nYou'll receive alerts every 10 mins (only if errors exist)."}}]
    send_slack(blocks, "Test from error reporter")


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Error PostHog → Slack Reporter")
    parser.add_argument("--test", action="store_true", help="Test Slack connection")
    args = parser.parse_args()
    
    if args.test:
        test_slack()
    else:
        check_errors()
