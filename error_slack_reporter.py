#!/usr/bin/env python3
"""
PostHog → Slack Error Reporter
==============================
Checks for errors every 10 mins and posts to Slack with error details and session replays.

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

# Error events to monitor
ERROR_EVENTS = {
    "app_error_captured": "🐛 App Error",
    "$exception": "💥 Exception",
    "buy_provider_availability_error": "💳 Payment Error",
    "send_transaction_result": "📤 Send Failure",
    "swap_execution_result": "🔄 Swap Failure",
    "$rageclick": "😤 Rage Click"
}

# Max errors to show per type (no limit)
MAX_ERRORS_PER_TYPE = 100


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


def get_error_details(date_from: str, date_to: str) -> dict:
    """Get detailed error info including messages and session IDs."""
    
    errors_by_type = {}
    
    for event, friendly_name in ERROR_EVENTS.items():
        # Build query based on event type to get relevant error message
        if event == "$exception":
            message_field = "properties.$exception_message"
        elif event == "app_error_captured":
            message_field = "properties.message"
        elif event == "buy_provider_availability_error":
            message_field = "concat(properties.error, ' (', properties.provider, ')')"
        elif event == "send_transaction_result":
            message_field = "concat(properties.error, ' | ', properties.hash)"
        elif event == "swap_execution_result":
            message_field = "properties.error"
        else:
            message_field = "properties.message"
        
        query = f"""
            SELECT 
                properties.$os as os,
                {message_field} as error_message,
                properties.$session_id as session_id,
                distinct_id,
                timestamp,
                properties.$current_url as url
            FROM events
            WHERE event = '{event}'
                AND timestamp >= '{date_from}' 
                AND timestamp < '{date_to}'
                AND (properties.$os = 'iOS' OR properties.$os = 'Android')
            ORDER BY timestamp DESC
            LIMIT 100
        """
        
        result = query_posthog(query)
        
        if result and result.get("results"):
            errors_by_type[event] = {
                "name": friendly_name,
                "errors": []
            }
            
            for row in result.get("results", []):
                os_name = row[0] or "Unknown"
                error_msg = row[1] or "No message"
                session_id = row[2]
                distinct_id = row[3]
                timestamp = row[4]
                url = row[5]
                
                # Truncate long error messages
                if len(str(error_msg)) > 100:
                    error_msg = str(error_msg)[:100] + "..."
                
                error_info = {
                    "os": os_name,
                    "message": error_msg,
                    "session_id": session_id,
                    "distinct_id": distinct_id,
                    "timestamp": timestamp,
                    "url": url
                }
                errors_by_type[event]["errors"].append(error_info)
            
            # Also store total count
            errors_by_type[event]["total"] = len(result.get("results", []))
    
    return errors_by_type


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


# =============================================================================
# ERROR REPORT
# =============================================================================

def check_errors():
    """Check for errors in last 10 mins and report with details."""
    check_config()
    
    now = datetime.utcnow()
    ten_mins_ago = now - timedelta(minutes=10)
    
    date_from = ten_mins_ago.strftime("%Y-%m-%d %H:%M:%S")
    date_to = now.strftime("%Y-%m-%d %H:%M:%S")
    
    print(f"🚨 Checking errors from {date_from} to {date_to}...")
    
    errors_by_type = get_error_details(date_from, date_to)
    
    if not errors_by_type:
        print("✅ No errors in last 10 minutes")
        return
    
    # Count total errors
    total_errors = sum(data["total"] for data in errors_by_type.values())
    
    if total_errors == 0:
        print("✅ No errors in last 10 minutes")
        return
    
    # Build Slack message
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"🚨 {total_errors} errors in last 10 min", "emoji": True}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"_{ten_mins_ago.strftime('%H:%M')} - {now.strftime('%H:%M UTC')}_"}]},
        {"type": "divider"},
    ]
    
    for event, data in errors_by_type.items():
        if not data["errors"]:
            continue
        
        # Section header for error type
        error_count = data["total"]
        
        header_text = f"*{data['name']}* ({error_count})"
        
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": header_text}})
        
        # Individual errors
        for err in data["errors"]:
            os_emoji = "🍎" if err["os"] == "iOS" else "🤖"
            
            # Build error line
            error_line = f"{os_emoji} `{err['message']}`"
            
            # Add session replay link if available
            if err["session_id"]:
                replay_url = get_session_replay_url(err["session_id"])
                error_line += f"\n     <{replay_url}|▶️ Watch Session>"
            
            blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": error_line}]})
        
        blocks.append({"type": "divider"})
    
    # Footer with dashboard link
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": 
        f"<{POSTHOG_HOST}/project/{POSTHOG_PROJECT_ID}/dashboard/859640|View Error Dashboard> · "
        f"<{POSTHOG_HOST}/project/{POSTHOG_PROJECT_ID}/events?eventType=$exception|View All Exceptions>"
    }]})
    
    send_slack(blocks, f"🚨 {total_errors} errors in last 10 min")


def test_slack():
    """Test connection with sample error format."""
    check_config()
    
    sample_replay_url = f"{POSTHOG_HOST}/project/{POSTHOG_PROJECT_ID}/replay/sample-session-id"
    
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": "✅ *Error reporter connected!*"}},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": "*Example error format:*"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "*💥 Exception* (3 total)"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": 
            f"🍎 `TypeError: Cannot read property 'x' of undefined`\n     <{sample_replay_url}|▶️ Watch Session>"
        }]},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": 
            f"🤖 `NetworkError: Request failed with status 500`\n     <{sample_replay_url}|▶️ Watch Session>"
        }]},
        {"type": "divider"},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": "You'll receive alerts every 10 mins (only if errors exist)."}]}
    ]
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
