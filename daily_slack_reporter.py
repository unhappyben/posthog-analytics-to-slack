#!/usr/bin/env python3
"""
PostHog → Slack Daily Reporter
==============================
Sends daily stats to Slack with real funnel conversions.

Environment variables:
    POSTHOG_API_KEY       - Your PostHog personal API key
    POSTHOG_PROJECT_ID    - Your PostHog project ID
    SLACK_WEBHOOK_DAILY   - Slack webhook for daily stats channel

Usage:
    python daily_slack_reporter.py
    python daily_slack_reporter.py --test
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
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_DAILY")

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
        missing.append("SLACK_WEBHOOK_DAILY")
    
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


def get_unique_users_by_os(event_name: str, date_from: str, date_to: str) -> dict:
    """Get unique users by OS."""
    query = f"""
        SELECT properties.$os as os, count(DISTINCT distinct_id) as users
        FROM events
        WHERE event = '{event_name}'
            AND timestamp >= '{date_from}' AND timestamp < '{date_to}'
            AND (properties.$os = 'iOS' OR properties.$os = 'Android')
        GROUP BY os ORDER BY users DESC
    """
    result = query_posthog(query)
    counts = {"iOS": 0, "Android": 0}
    if result:
        for row in result.get("results", []):
            if row[0] in counts:
                counts[row[0]] = row[1]
    return counts


def get_real_funnel_conversion(start_event: str, end_event: str, date_from: str, date_to: str) -> dict:
    """
    Get REAL funnel conversion - users who did start event AND end event.
    Returns: {os: {started: X, completed: Y, rate: Z%}}
    """
    query = f"""
        SELECT 
            start_os as os,
            count() as started,
            countIf(completed = 1) as completed
        FROM (
            SELECT 
                distinct_id,
                argMin(properties.$os, timestamp) as start_os,
                max(CASE WHEN event = '{end_event}' THEN 1 ELSE 0 END) as completed
            FROM events
            WHERE 
                event IN ('{start_event}', '{end_event}')
                AND timestamp >= '{date_from}' 
                AND timestamp < '{date_to}'
                AND (properties.$os = 'iOS' OR properties.$os = 'Android')
            GROUP BY distinct_id
            HAVING sum(CASE WHEN event = '{start_event}' THEN 1 ELSE 0 END) > 0
        )
        GROUP BY start_os
        ORDER BY started DESC
    """
    
    result = query_posthog(query)
    
    funnel = {"iOS": {"started": 0, "completed": 0, "rate": 0}, 
              "Android": {"started": 0, "completed": 0, "rate": 0}}
    
    if result:
        for row in result.get("results", []):
            os_name = row[0]
            if os_name in funnel:
                started = row[1]
                completed = row[2]
                rate = round((completed / started) * 100, 1) if started > 0 else 0
                funnel[os_name] = {"started": started, "completed": completed, "rate": rate}
    
    return funnel


def get_error_summary(date_from: str, date_to: str) -> dict:
    """Get error counts for the day."""
    error_events = {
        "app_error_captured": "App Errors",
        "$exception": "Exceptions",
        "buy_provider_availability_error": "Payment Errors",
        "$rageclick": "Rage Clicks"
    }
    
    errors = {}
    for event, name in error_events.items():
        counts = get_event_count_by_os(event, date_from, date_to)
        total = counts["iOS"] + counts["Android"]
        if total > 0:
            errors[name] = {"iOS": counts["iOS"], "Android": counts["Android"], "total": total}
    
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


def fmt_num(n: int) -> str:
    return f"{n:,}"


def fmt_change(current: int, previous: int) -> str:
    if previous == 0:
        return "🆕" if current > 0 else "—"
    change = ((current - previous) / previous) * 100
    emoji = "📈" if change > 0 else "📉" if change < 0 else "➡️"
    return f"{emoji} {change:+.1f}%"


def fmt_funnel(funnel: dict, os: str) -> str:
    """Format funnel as: 87.5% (8→7)"""
    f = funnel[os]
    if f['started'] == 0:
        return "—"
    return f"*{f['rate']}%* ({f['started']}→{f['completed']})"


# =============================================================================
# DAILY REPORT
# =============================================================================

def generate_daily_report():
    """Generate and send daily stats."""
    check_config()
    
    today = datetime.utcnow().date()
    yesterday = today - timedelta(days=1)
    day_before = today - timedelta(days=2)
    
    date_from, date_to = yesterday.isoformat(), today.isoformat()
    prev_from, prev_to = day_before.isoformat(), yesterday.isoformat()
    
    print(f"📊 Generating daily report for {yesterday}...")
    
    # ===================
    # METRICS
    # ===================
    
    # DAU
    dau = get_unique_users_by_os("app_launched", date_from, date_to)
    dau_prev = get_unique_users_by_os("app_launched", prev_from, prev_to)
    
    # Buy completions (raw count)
    buy = get_event_count_by_os("buy_payment_state_changed", date_from, date_to)
    buy_prev = get_event_count_by_os("buy_payment_state_changed", prev_from, prev_to)
    
    # Buy funnel - Standard flow (form → complete)
    buy_funnel = get_real_funnel_conversion("buy_form_viewed", "buy_payment_state_changed", date_from, date_to)
    
    # Buy funnel - Deeplink flow (deeplink → complete)
    deeplink_funnel = get_real_funnel_conversion("deeplink_intent_viewed", "buy_payment_state_changed", date_from, date_to)
    
    # Onboarding completions (raw count)
    onboard = get_event_count_by_os("auth_session_ready", date_from, date_to)
    onboard_prev = get_event_count_by_os("auth_session_ready", prev_from, prev_to)
    
    # Onboarding funnel (real conversion)
    onboard_funnel = get_real_funnel_conversion("auth_login_screen_viewed", "auth_session_ready", date_from, date_to)
    
    # Errors summary
    errors = get_error_summary(date_from, date_to)
    
    # ===================
    # CALCULATIONS
    # ===================
    
    # Total buy completions
    total_buy = buy['iOS'] + buy['Android']
    total_buy_prev = buy_prev['iOS'] + buy_prev['Android']
    
    # Buy via standard funnel
    standard_buy = buy_funnel['iOS']['completed'] + buy_funnel['Android']['completed']
    
    # Buy via deeplink
    deeplink_buy = deeplink_funnel['iOS']['completed'] + deeplink_funnel['Android']['completed']
    
    # Other buys (not through standard or deeplink)
    other_buy = total_buy - standard_buy - deeplink_buy
    if other_buy < 0:
        other_buy = 0  # Some users might be in both funnels
    
    # ===================
    # BUILD SLACK MESSAGE
    # ===================
    
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"📊 Daily Mobile Stats — {yesterday.strftime('%A, %B %d, %Y')}", "emoji": True}},
        {"type": "divider"},
        
        # DAU
        {"type": "section", "text": {"type": "mrkdwn", "text": 
            f"*👥 Daily Active Users*\n"
            f"• iOS: *{fmt_num(dau['iOS'])}* {fmt_change(dau['iOS'], dau_prev['iOS'])}\n"
            f"• Android: *{fmt_num(dau['Android'])}* {fmt_change(dau['Android'], dau_prev['Android'])}\n"
            f"• Total: *{fmt_num(dau['iOS'] + dau['Android'])}*"
        }},
        {"type": "divider"},
        
        # Buy Section
        {"type": "section", "text": {"type": "mrkdwn", "text": 
            f"*💰 Buy Completions*\n"
            f"• iOS: *{fmt_num(buy['iOS'])}* {fmt_change(buy['iOS'], buy_prev['iOS'])}\n"
            f"• Android: *{fmt_num(buy['Android'])}* {fmt_change(buy['Android'], buy_prev['Android'])}\n"
            f"• Total: *{fmt_num(total_buy)}* {fmt_change(total_buy, total_buy_prev)}"
        }},
        
        # Buy Funnels breakdown
        {"type": "context", "elements": [{"type": "mrkdwn", "text": 
            f"*📊 Standard Flow* (buy_form_viewed → complete)\n"
            f"iOS: {fmt_funnel(buy_funnel, 'iOS')} · Android: {fmt_funnel(buy_funnel, 'Android')}\n\n"
            f"*🔗 Deeplink Flow* (deeplink → complete)\n"
            f"iOS: {fmt_funnel(deeplink_funnel, 'iOS')} · Android: {fmt_funnel(deeplink_funnel, 'Android')}\n\n"
            f"_Standard: {standard_buy} · Deeplink: {deeplink_buy} · Other: {other_buy}_"
        }]},
        {"type": "divider"},
        
        # Onboarding Section
        {"type": "section", "text": {"type": "mrkdwn", "text": 
            f"*🚀 Onboarding Completions*\n"
            f"• iOS: *{fmt_num(onboard['iOS'])}* {fmt_change(onboard['iOS'], onboard_prev['iOS'])}\n"
            f"• Android: *{fmt_num(onboard['Android'])}* {fmt_change(onboard['Android'], onboard_prev['Android'])}\n"
            f"• Total: *{fmt_num(onboard['iOS'] + onboard['Android'])}*"
        }},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": 
            f"*📊 Funnel Conversion* (login_screen → session_ready)\n"
            f"iOS: {fmt_funnel(onboard_funnel, 'iOS')} · Android: {fmt_funnel(onboard_funnel, 'Android')}"
        }]},
        {"type": "divider"},
    ]
    
    # Error Summary
    if errors:
        error_lines = []
        for name, counts in errors.items():
            error_lines.append(f"• {name}: iOS *{counts['iOS']}* · Android *{counts['Android']}*")
        
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": 
            f"*⚠️ Yesterday's Issues*\n" + "\n".join(error_lines)
        }})
    else:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": 
            "*✅ No errors yesterday!*"
        }})
    
    blocks.append({"type": "divider"})
    
    # Links
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": 
        f"<https://app.posthog.com/project/{POSTHOG_PROJECT_ID}/dashboard/859544|Buy Dashboard> · "
        f"<https://app.posthog.com/project/{POSTHOG_PROJECT_ID}/dashboard/859543|Onboarding Dashboard> · "
        f"<https://app.posthog.com/project/{POSTHOG_PROJECT_ID}/dashboard/859640|Errors>"
    }]})
    
    send_slack(blocks, f"Daily Mobile Stats — {yesterday}")


def test_slack():
    """Test connection."""
    check_config()
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": 
        "✅ *Daily reporter connected!*\n\n"
        "You'll receive:\n"
        "• 👥 DAU (iOS/Android)\n"
        "• 💰 Buy completions + funnel breakdown\n"
        "  → Standard flow conversion\n"
        "  → Deeplink flow conversion\n"
        "• 🚀 Onboarding completions + conversion\n"
        "• ⚠️ Error summary\n\n"
        "Every morning at 9am CET."
    }}]
    send_slack(blocks, "Test from daily reporter")


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Daily PostHog → Slack Reporter")
    parser.add_argument("--test", action="store_true", help="Test Slack connection")
    args = parser.parse_args()
    
    if args.test:
        test_slack()
    else:
        generate_daily_report()
