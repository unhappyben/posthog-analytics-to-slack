# PostHog → Slack Daily Digest

Automated daily Slack notifications with your key PostHog metrics. Free forever.

## What it does

Every weekday at 9am, posts a digest like this to Slack:

```
📊 Daily PostHog Digest — Thursday, December 12

🚨 Error Monitoring
• Payment Errors (24h): 12
• API Failures: 3
• Provider Timeouts: 2

💰 Buy Flow Performance  
• Buy Funnel Conversion: 23.4%
• Completed Purchases: 156

🖥️ UI & UX Health
• Rage Clicks (24h): 8
• Dead Clicks: 23
```

## Setup (10 mins)

### 1. Get your PostHog API key

1. Go to PostHog → Settings → Personal API Keys
2. Create a new key with `Read` access
3. Copy the key (starts with `phx_`)

### 2. Create a Slack webhook

1. Go to https://api.slack.com/apps
2. Click **Create New App** → **From scratch**
3. Name it "PostHog Digest", pick your workspace
4. Go to **Incoming Webhooks** → Enable it
5. Click **Add New Webhook to Workspace**
6. Pick the channel (e.g., `#product-metrics`)
7. Copy the webhook URL

### 3. Set up GitHub repo

1. Create a new GitHub repo (or use an existing one)
2. Add these files:
   - `posthog_to_slack.py` (the main script)
   - `.github/workflows/daily-digest.yml` (the scheduler)

3. Add secrets in GitHub:
   - Go to repo → Settings → Secrets and variables → Actions
   - Add these **Secrets** (sensitive):
     - `POSTHOG_API_KEY` — your PostHog API key
     - `POSTHOG_PROJECT_ID` — `233883`
     - `SLACK_WEBHOOK_URL` — the Slack webhook URL
   
   - Add these **Variables** (not sensitive, easier to edit):
     - `DASHBOARD_ERROR_MONITORING` — `859640`
     - `DASHBOARD_BUY_FLOW` — `859544`
     - `DASHBOARD_UI_UX_HEALTH` — `859641`
     - `POSTHOG_HOST` — `https://app.posthog.com` (optional, this is the default)

### 4. Test it

1. Go to Actions tab in your repo
2. Click "Daily PostHog Digest" workflow
3. Click "Run workflow" → "Run workflow"
4. Check your Slack channel 🎉

## Customize

### Change the schedule

Edit `.github/workflows/daily-digest.yml`:

```yaml
schedule:
  # Current: 9am UTC, weekdays only
  - cron: '0 9 * * 1-5'
  
  # Examples:
  # 8am UTC daily:        '0 8 * * *'
  # 10am UTC Mon-Fri:     '0 10 * * 1-5'
  # 6pm UTC (end of day): '0 18 * * 1-5'
```

### Change which dashboards are included

Update the GitHub **Variables** (not secrets):
- `DASHBOARD_ERROR_MONITORING` — set to your dashboard ID or remove to exclude
- `DASHBOARD_BUY_FLOW` — set to your dashboard ID or remove to exclude  
- `DASHBOARD_UI_UX_HEALTH` — set to your dashboard ID or remove to exclude

To add more dashboards, you'll need to edit `posthog_to_slack.py` and add new env vars.

### Get dashboard/insight IDs

The ID is in the URL when viewing a dashboard:
```
https://app.posthog.com/project/233883/dashboard/859640
                                                 ^^^^^^
                                                 This is the ID
```

## Troubleshooting

**"POSTHOG_API_KEY environment variable required"**
→ Check your GitHub secrets are set correctly

**Slack message not posting**
→ Test the webhook URL with curl:
```bash
curl -X POST -H 'Content-type: application/json' \
  --data '{"text":"Test message"}' \
  YOUR_WEBHOOK_URL
```

**Metrics showing "N/A"**
→ The insight might be a type the script doesn't handle yet. Open an issue!

## Local testing

```bash
export POSTHOG_API_KEY="phx_your_key"
export POSTHOG_PROJECT_ID="233883"
export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/xxx"
export DASHBOARD_ERROR_MONITORING="859640"
export DASHBOARD_BUY_FLOW="859544"
export DASHBOARD_UI_UX_HEALTH="859641"

python posthog_to_slack.py
```
