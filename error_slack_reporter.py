#!/usr/bin/env python3
"""
PostHog → Slack Focused Error Reporter
=======================================
Checks for specific error conditions every 10 mins and posts to Slack.

Environment variables:
    POSTHOG_API_KEY       - Your PostHog personal API key
    POSTHOG_PROJECT_ID    - Your PostHog project ID
    SLACK_WEBHOOK_ERRORS  - Slack webhook for errors channel

Usage:
    python error_reporter.py
    python error_reporter.py --test
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

# =============================================================================
# ERROR DEFINITIONS
# Each error has:
#   - event: PostHog event name
#   - name: Friendly name for Slack
#   - emoji: Display emoji
#   - filter: SQL WHERE condition for the error state (None = any occurrence)
#   - properties: List of property names to display in Slack
# =============================================================================

ERROR_DEFINITIONS = [
    # Auth errors
    {
        "event": "auth_invite_code_result",
        "name": "Invite Code Error",
        "emoji": "🔑",
        "filter": "properties.status IN ('invalid', 'error')",
        "properties": ["status", "error"]
    },
    {
        "event": "auth_otp_result",
        "name": "OTP Error",
        "emoji": "🔢",
        "filter": "properties.status = 'error'",
        "properties": ["status", "error"]
    },
    {
        "event": "auth_consent_decision",
        "name": "Consent Denied",
        "emoji": "🚫",
        "filter": "properties.decision = 'deny'",
        "properties": ["decision"]
    },
    {
        "event": "auth_logout_failed",
        "name": "Logout Failed",
        "emoji": "🚪",
        "filter": None,  # Any occurrence
        "properties": ["error"]
    },
    
    # Buy errors
    {
        "event": "buy_provider_availability_error",
        "name": "Provider Availability Error",
        "emoji": "💳",
        "filter": None,  # Any occurrence
        "properties": ["provider", "error"]
    },
    {
        "event": "buy_payment_state_changed",
        "name": "Payment Failed",
        "emoji": "💰",
        "filter": "properties.state = 'failed'",
        "properties": ["state", "error", "provider"]
    },
    
    # Send errors
    {
        "event": "send_validation_error",
        "name": "Send Validation Error",
        "emoji": "📤",
        "filter": None,  # Any occurrence
        "properties": ["reason"]
    },
    {
        "event": "send_recipient_validation_result",
        "name": "Recipient Validation Error",
        "emoji": "👤",
        "filter": "properties.status IN ('invalid', 'error')",
        "properties": ["status", "error"]
    },
    {
        "event": "send_transaction_result",
        "name": "Send Transaction Error",
        "emoji": "📤",
        "filter": "properties.status IN ('error', 'cancelled')",
        "properties": ["status", "error"]
    },
    
    # Sell errors
    {
        "event": "sell_result",
        "name": "Sell Error",
        "emoji": "💵",
        "filter": "properties.status = 'error'",
        "properties": ["status", "provider", "error"]
    },
    
    # Swap errors
    {
        "event": "swap_transfer_result",
        "name": "Swap Transfer Error",
        "emoji": "🔄",
        "filter": "properties.status = 'error'",
        "properties": ["status", "error"]
    },
    {
        "event": "swap_execution_result",
        "name": "Swap Execution Error",
        "emoji": "🔄",
        "filter": "properties.status = 'error'",
        "properties": ["status", "error"]
    },
    {
        "event": "asset_swap_validation_error",
        "name": "Swap Validation Error",
        "emoji": "⚠️",
        "filter": None,  # Any occurrence
        "properties": ["reason"]
    },
    
    # Transaction errors
    {
        "event": "transaction_cancel_result",
        "name": "Transaction Cancel Error",
        "emoji": "❌",
        "filter": "properties.status = 'error'",
        "properties": ["status", "error"]
    },
    
    # Profile errors
    {
        "event": "profile_biometric_toggled",
        "name": "Biometric Toggle Failed",
        "emoji": "👆",
        "filter": "properties.result = 'failed'",
        "properties": ["result", "error"]
    },
    {
        "event": "edit_profile_email_update_result",
        "name": "Email Update Error",
        "emoji": "📧",
        "filter": "properties.status = 'error'",
        "properties": ["status", "error"]
    },
    
    # Deeplink errors
    {
        "event": "deeplink_intent_action",
        "name": "Deeplink Error",
        "emoji": "🔗",
        "filter": "properties.action = 'expired' OR properties.reason = 'invalid'",
        "properties": ["action", "reason"]
    },
    
    # Platform errors
    {
        "event": "app_error_captured",
        "name": "App Error",
        "emoji": "🐛",
        "filter": "properties.severity = 'error'",
        "properties": ["message", "source", "severity"]
    },
    {
        "event": "ui_toast_shown",
        "name": "Error Toast",
        "emoji": "🍞",
        "filter": "properties.tone = 'error'",
        "properties": ["toast_id", "message"]
    },
]

MAX_ERRORS_PER_TYPE = 10  # Limit per error type to avoid huge messages


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


def get_errors_for_event(error_def: dict, date_from: str, date_to: str) -> list:
    """Get errors for a specific event definition."""
    
    event = error_def["event"]
    filter_condition = error_def["filter"]
    props_to_fetch = error_def["properties"]
    
    # Build property select fields
    prop_selects = ", ".join([f"properties.{p} as {p}" for p in props_to_fetch])
    
    # Build WHERE clause
    where_parts = [
        f"event = '{event}'",
        f"timestamp >= '{date_from}'",
        f"timestamp < '{date_to}'",
        "(properties.$os = 'iOS' OR properties.$os = 'Android')"
    ]
    
    if filter_condition:
        where_parts.append(f"({filter_condition})")
    
    where_clause = " AND ".join(where_parts)
    
    query = f"""
        SELECT 
            properties.$os as os,
            properties.$session_id as session_id,
            timestamp,
            {prop_selects}
        FROM events
        WHERE {where_clause}
        ORDER BY timestamp DESC
        LIMIT {MAX_ERRORS_PER_TYPE}
    """
    
    result = query_posthog(query)
    
    errors = []
    if result and result.get("results"):
        # Get column names from result
        columns = result.get("columns", [])
        
        for row in result.get("results", []):
            error = {}
            for i, col in enumerate(columns):
                error[col] = row[i]
            errors.append(error)
    
    return errors


def get_session_replay_url(session_id: str) -> str:
    """Generate PostHog session replay URL."""
    if not session_id:
        return None
    return f"{POSTHOG_HOST}/project/{POSTHOG_PROJECT_ID}/replay/{session_id}"


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


def format_error_properties(error: dict, props_to_show: list) -> str:
    """Format error properties for display."""
    parts = []
    for prop in props_to_show:
        value = error.get(prop)
        if value:
            # Truncate long values
            value_str = str(value)
            if len(value_str) > 80:
                value_str = value_str[:80] + "..."
            parts.append(f"{prop}=`{value_str}`")
    return " · ".join(parts) if parts else "No details"


# =============================================================================
# ERROR REPORT
# =============================================================================

def check_errors():
    """Check for specific errors in last 10 mins and report."""
    check_config()
    
    now = datetime.utcnow()
    ten_mins_ago = now - timedelta(minutes=10)
    
    date_from = ten_mins_ago.strftime("%Y-%m-%d %H:%M:%S")
    date_to = now.strftime("%Y-%m-%d %H:%M:%S")
    
    print(f"🚨 Checking errors from {date_from} to {date_to}...")
    
    # Collect all errors
    all_errors = {}
    total_error_count = 0
    
    for error_def in ERROR_DEFINITIONS:
        errors = get_errors_for_event(error_def, date_from, date_to)
        if errors:
            all_errors[error_def["event"]] = {
                "definition": error_def,
                "errors": errors
            }
            total_error_count += len(errors)
            print(f"  Found {len(errors)} {error_def['name']}")
    
    if total_error_count == 0:
        print("✅ No errors in last 10 minutes")
        return
    
    # Build Slack message
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"🚨 {total_error_count} errors in last 10 min", "emoji": True}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"_{ten_mins_ago.strftime('%H:%M')} - {now.strftime('%H:%M UTC')}_"}]},
        {"type": "divider"},
    ]
    
    for event_name, data in all_errors.items():
        error_def = data["definition"]
        errors = data["errors"]
        
        # Section header
        header_text = f"*{error_def['emoji']} {error_def['name']}* ({len(errors)})"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": header_text}})
        
        # Individual errors
        for err in errors:
            os_emoji = "🍎" if err.get("os") == "iOS" else "🤖"
            props_text = format_error_properties(err, error_def["properties"])
            
            error_line = f"{os_emoji} {props_text}"
            
            # Add session replay link if available
            session_id = err.get("session_id")
            if session_id:
                replay_url = get_session_replay_url(session_id)
                error_line += f"\n     <{replay_url}|▶️ Watch Session>"
            
            blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": error_line}]})
        
        blocks.append({"type": "divider"})
    
    # Footer with dashboard link
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": 
        f"<{POSTHOG_HOST}/project/{POSTHOG_PROJECT_ID}/dashboard/859640|View Error Dashboard>"
    }]})
    
    send_slack(blocks, f"🚨 {total_error_count} errors in last 10 min")


def test_slack():
    """Test connection with sample error format."""
    check_config()
    
    sample_replay_url = f"{POSTHOG_HOST}/project/{POSTHOG_PROJECT_ID}/replay/sample-session-id"
    
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": "✅ *Focused error reporter connected!*"}},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": "*Monitoring these error conditions:*"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": 
            "🔑 Invite Code Error · 🔢 OTP Error · 🚫 Consent Denied · 🚪 Logout Failed\n"
            "💳 Provider Availability · 💰 Payment Failed\n"
            "📤 Send Validation · 👤 Recipient Validation · 📤 Send Transaction\n"
            "💵 Sell Error · 🔄 Swap Errors · ⚠️ Swap Validation\n"
            "❌ Transaction Cancel · 👆 Biometric Failed · 📧 Email Update\n"
            "🔗 Deeplink Error · 🐛 App Error · 🍞 Error Toast"
        }]},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": "*Example error format:*"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "*💳 Provider Availability Error* (2)"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": 
            f"🍎 provider=`moonpay` · error=`timeout`\n     <{sample_replay_url}|▶️ Watch Session>"
        }]},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": 
            f"🤖 provider=`ramp` · error=`service_unavailable`\n     <{sample_replay_url}|▶️ Watch Session>"
        }]},
        {"type": "divider"},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": "Alerts every 10 mins (only if errors exist)."}]}
    ]
    send_slack(blocks, "Test from focused error reporter")


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Focused Error PostHog → Slack Reporter")
    parser.add_argument("--test", action="store_true", help="Test Slack connection")
    args = parser.parse_args()
    
    if args.test:
        test_slack()
    else:
        check_errors()
