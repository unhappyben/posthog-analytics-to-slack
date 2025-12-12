#!/usr/bin/env python3
"""
PostHog Daily Digest → Slack
Fetches key metrics from PostHog dashboards and posts a summary to Slack.
"""

import os
import requests
from datetime import datetime, timedelta

# Config from environment
POSTHOG_API_KEY = os.environ.get("POSTHOG_API_KEY")
POSTHOG_PROJECT_ID = os.environ.get("POSTHOG_PROJECT_ID")
POSTHOG_HOST = os.environ.get("POSTHOG_HOST", "https://app.posthog.com")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")

# Dashboard IDs from environment
DASHBOARD_ERROR_MONITORING = os.environ.get("DASHBOARD_ERROR_MONITORING")
DASHBOARD_BUY_FLOW = os.environ.get("DASHBOARD_BUY_FLOW")
DASHBOARD_UI_UX_HEALTH = os.environ.get("DASHBOARD_UI_UX_HEALTH")


def get_dashboards():
    """Build dashboards dict from environment variables."""
    dashboards = {}
    
    if DASHBOARD_ERROR_MONITORING:
        dashboards["🚨 Error Monitoring"] = {
            "id": DASHBOARD_ERROR_MONITORING,
            "url": f"{POSTHOG_HOST}/project/{POSTHOG_PROJECT_ID}/dashboard/{DASHBOARD_ERROR_MONITORING}"
        }
    
    if DASHBOARD_BUY_FLOW:
        dashboards["💰 Buy Flow Performance"] = {
            "id": DASHBOARD_BUY_FLOW,
            "url": f"{POSTHOG_HOST}/project/{POSTHOG_PROJECT_ID}/dashboard/{DASHBOARD_BUY_FLOW}"
        }
    
    if DASHBOARD_UI_UX_HEALTH:
        dashboards["🖥️ UI & UX Health"] = {
            "id": DASHBOARD_UI_UX_HEALTH,
            "url": f"{POSTHOG_HOST}/project/{POSTHOG_PROJECT_ID}/dashboard/{DASHBOARD_UI_UX_HEALTH}"
        }
    
    return dashboards


def get_headers():
    return {
        "Authorization": f"Bearer {POSTHOG_API_KEY}",
        "Content-Type": "application/json"
    }


def fetch_dashboard(dashboard_id):
    """Fetch dashboard details and its insights."""
    url = f"{POSTHOG_HOST}/api/projects/{POSTHOG_PROJECT_ID}/dashboards/{dashboard_id}"
    response = requests.get(url, headers=get_headers())
    response.raise_for_status()
    return response.json()


def fetch_insight_results(insight_id):
    """Fetch the actual results for an insight."""
    url = f"{POSTHOG_HOST}/api/projects/{POSTHOG_PROJECT_ID}/insights/{insight_id}"
    response = requests.get(url, headers=get_headers())
    response.raise_for_status()
    return response.json()


def extract_funnel_conversion(insight_data):
    """Extract conversion rate from a funnel insight."""
    try:
        results = insight_data.get("result", [])
        if results and len(results) > 0:
            # Funnel results have steps with counts
            steps = results[0] if isinstance(results[0], list) else results
            if len(steps) >= 2:
                first_step = steps[0].get("count", 0)
                last_step = steps[-1].get("count", 0)
                if first_step > 0:
                    conversion = (last_step / first_step) * 100
                    return f"{conversion:.1f}%"
    except Exception:
        pass
    return "N/A"


def extract_trend_value(insight_data):
    """Extract the latest value from a trends insight."""
    try:
        results = insight_data.get("result", [])
        if results and len(results) > 0:
            data = results[0].get("data", [])
            if data:
                return f"{data[-1]:,.0f}"
    except Exception:
        pass
    return "N/A"


def extract_metric(insight_data):
    """Extract key metric from an insight based on its type."""
    filters = insight_data.get("filters", {})
    insight_type = filters.get("insight", "TRENDS")
    
    if insight_type == "FUNNELS":
        return extract_funnel_conversion(insight_data)
    else:
        return extract_trend_value(insight_data)


def build_dashboard_summary(dashboard_name, dashboard_info):
    """Build a summary for a single dashboard."""
    try:
        dashboard_data = fetch_dashboard(dashboard_info["id"])
        tiles = dashboard_data.get("tiles", [])
        
        metrics = []
        for tile in tiles[:5]:  # Limit to first 5 insights per dashboard
            insight = tile.get("insight")
            if insight:
                insight_name = insight.get("name", "Unnamed")
                insight_id = insight.get("id")
                
                if insight_id:
                    try:
                        insight_data = fetch_insight_results(insight_id)
                        value = extract_metric(insight_data)
                        metrics.append(f"• {insight_name}: *{value}*")
                    except Exception as e:
                        metrics.append(f"• {insight_name}: _error fetching_")
        
        if metrics:
            return f"*<{dashboard_info['url']}|{dashboard_name}>*\n" + "\n".join(metrics)
        else:
            return f"*<{dashboard_info['url']}|{dashboard_name}>*\n• _No insights found_"
            
    except Exception as e:
        return f"*<{dashboard_info['url']}|{dashboard_name}>*\n• _Error: {str(e)}_"


def build_slack_message():
    """Build the full Slack message."""
    today = datetime.now().strftime("%A, %B %d")
    dashboards = get_dashboards()
    
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"📊 Daily PostHog Digest — {today}",
                "emoji": True
            }
        },
        {
            "type": "divider"
        }
    ]
    
    for dashboard_name, dashboard_info in dashboards.items():
        summary = build_dashboard_summary(dashboard_name, dashboard_info)
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": summary
            }
        })
        blocks.append({"type": "divider"})
    
    # Footer
    blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": f"Automated daily digest from PostHog • <{POSTHOG_HOST}/project/{POSTHOG_PROJECT_ID}|Open PostHog>"
            }
        ]
    })
    
    return {"blocks": blocks}


def post_to_slack(message):
    """Post the message to Slack."""
    response = requests.post(
        SLACK_WEBHOOK_URL,
        json=message,
        headers={"Content-Type": "application/json"}
    )
    response.raise_for_status()
    print("✅ Posted to Slack successfully")


def main():
    # Validate config
    if not POSTHOG_API_KEY:
        raise ValueError("POSTHOG_API_KEY environment variable required")
    if not POSTHOG_PROJECT_ID:
        raise ValueError("POSTHOG_PROJECT_ID environment variable required")
    if not SLACK_WEBHOOK_URL:
        raise ValueError("SLACK_WEBHOOK_URL environment variable required")
    
    dashboards = get_dashboards()
    if not dashboards:
        raise ValueError("At least one DASHBOARD_* environment variable required")
    
    print(f"🔄 Fetching PostHog data for {len(dashboards)} dashboards...")
    message = build_slack_message()
    
    print("📤 Posting to Slack...")
    post_to_slack(message)
    
    print("✅ Done!")


if __name__ == "__main__":
    main()
