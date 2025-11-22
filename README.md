# 🔔 Notification Sender

Streamlit-based Firebase Cloud Messaging (FCM) notification sender with campaign tracking and cohort management.

## ✅ What's Working

- ✅ Send FCM notifications via Firebase Admin SDK
- ✅ Cohort management with AND/OR logic  
- ✅ Campaign tracking with unique IDs and custom names
- ✅ Local analytics in `campaigns.json`
- ✅ Visual cohort metrics showing CP ID counts

## ⚠️ Firebase Analytics Tracking

**Important:** To track notifications in Firebase Analytics/BigQuery, you **MUST add code to your React Native app**.

👉 **See `REACT_NATIVE_TRACKING_SETUP.md`** for complete implementation guide.

**Why?** Firebase doesn't automatically log notification data. The tool sends `campaign_id` and `campaign_name` in the notification payload, but your app must explicitly call `analytics().logEvent()` when users click notifications.

## 🚀 Quick Start

### Run the Tool
```powershell
.\.venv\Scripts\python.exe -m streamlit run NotificationSender.py
```

### Send Notifications
1. **Compose** tab → Enter title, body, campaign name
2. Select cohorts (Test, Premium, etc.)
3. Choose AND/OR logic
4. Click **Send Notification**

### View Local Analytics
- **Analytics** tab → Campaign history with send stats

### Enable BigQuery Tracking
- Read `REACT_NATIVE_TRACKING_SETUP.md`
- Add notification handler to React Native app
- Deploy update
- Wait 24-48 hours for BigQuery sync

## How It Works

### 1. Create Cohorts (Tab 2: Cohort Manager)
- Create cohorts like "North Bangalore", "Price 1-3cr", etc.
- Add CP IDs to each cohort
- Example: North Bangalore = [CP001, CP002, CP003]

### 2. Send Notifications (Tab 1: Send)
Choose one of 3 options:
- **🌍 All Users** - Fetches all CP IDs from Firestore `channel_partners` collection
- **🏷️ Specific Cohorts** - Select cohorts, use AND/OR logic, loads tokens for those CP IDs
- **📝 Manual Input** - Paste tokens or upload CSV

### 3. Track Campaigns (Tab 3: Analytics)
- View sent campaigns with campaign_id
- Filter by cohort or date
- Download CSV
- Track clicks in Firebase Analytics using campaign_id

## Data Storage

### `notification_data/cohorts.json`
```json
{
  "North Bangalore": ["CP001", "CP002", "CP003"],
  "Price 1-3cr": ["CP001", "CP004", "CP005"],
  "South Bangalore": ["CP006", "CP007"]
}
```

### `notification_data/campaigns.json`
```json
[{
  "campaign_id": "campaign_20251105_143022",
  "title": "New Properties",
  "body": "Check out...",
  "cohorts": ["North Bangalore", "Price 1-3cr"],
  "logic": "AND",
  "timestamp": "2025-11-05T14:30:22",
  "total_sent": 150,
  "total_failed": 5,
  "total_recipients": 155
}]
```

## AND/OR Logic

- **AND**: CP ID must be in ALL selected cohorts
  - Example: "North Bangalore" AND "Price 1-3cr" = only CP001 (appears in both)
- **OR**: CP ID in ANY selected cohort
  - Example: "North Bangalore" OR "Price 1-3cr" = CP001, CP002, CP003, CP004, CP005

## Run

```bash
.\.venv\Scripts\Activate.ps1
python -m streamlit run NotificationSender.py
```

**URL**: http://localhost:8501
