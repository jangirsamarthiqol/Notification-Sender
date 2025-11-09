# 🔔 React Native - Firebase Analytics Tracking Setup

## Problem
Your notification tool sends notifications successfully, but Firebase Analytics shows **NULL** for `firebase_message_id` and `firebase_message_name` in BigQuery.

## Why This Happens
- ✅ **Tool sends** notification data: `campaign_id`, `campaign_name`, `message_id`, `message_name`, `cohort_tags`
- ❌ **App doesn't log** this data to Firebase Analytics when notification is clicked
- ℹ️ Firebase Console works because their demo apps have tracking code pre-built

## Solution: Add This Code to Your React Native App

---

## 📦 Step 1: Install Required Packages

```bash
npm install @react-native-firebase/messaging @react-native-firebase/analytics
# or
yarn add @react-native-firebase/messaging @react-native-firebase/analytics
```

---

## 📝 Step 2: Add Notification Tracking Code

### Option A: If you already have a notification handler

**Find your existing notification handler** (usually in `App.js`, `index.js`, or a dedicated notification service file) and add the analytics logging:

```javascript
import messaging from '@react-native-firebase/messaging';
import analytics from '@react-native-firebase/analytics';

// Add this helper function
async function logNotificationOpen(remoteMessage) {
  if (!remoteMessage || !remoteMessage.data) return;
  
  const { data } = remoteMessage;
  
  // Log to Firebase Analytics
  await analytics().logEvent('notification_open', {
    message_id: data.campaign_id || data.message_id || '',
    message_name: data.campaign_name || data.message_name || remoteMessage.notification?.title || '',
    campaign_id: data.campaign_id || '',
    campaign_name: data.campaign_name || '',
    cohort_tags: data.cohort_tags || '',
    screen: data.screen || '',
    route: data.route || '',
  });
  
  console.log('📊 Logged notification_open to Firebase Analytics');
}

// In your existing notification setup, add these listeners:

// Background/Quit state - Notification opened app
messaging().onNotificationOpenedApp(async (remoteMessage) => {
  console.log('Notification caused app to open from background:', remoteMessage);
  
  // LOG TO ANALYTICS - THIS IS THE KEY!
  await logNotificationOpen(remoteMessage);
  
  // ... your existing navigation code ...
});

// Check if app was opened from a notification (when app was quit)
messaging()
  .getInitialNotification()
  .then(async (remoteMessage) => {
    if (remoteMessage) {
      console.log('Notification caused app to open from quit state:', remoteMessage);
      
      // LOG TO ANALYTICS - THIS IS THE KEY!
      await logNotificationOpen(remoteMessage);
      
      // ... your existing navigation code ...
    }
  });
```

---

### Option B: Complete Setup from Scratch

**Create a new file: `src/services/NotificationService.js`**

```javascript
import messaging from '@react-native-firebase/messaging';
import analytics from '@react-native-firebase/analytics';

class NotificationService {
  constructor() {
    this.setupNotificationHandlers();
  }

  async logNotificationOpen(remoteMessage) {
    if (!remoteMessage || !remoteMessage.data) return;
    
    const { data } = remoteMessage;
    
    // Log to Firebase Analytics - REQUIRED FOR TRACKING!
    await analytics().logEvent('notification_open', {
      message_id: data.campaign_id || data.message_id || '',
      message_name: data.campaign_name || data.message_name || remoteMessage.notification?.title || '',
      campaign_id: data.campaign_id || '',
      campaign_name: data.campaign_name || '',
      cohort_tags: data.cohort_tags || '',
      screen: data.screen || '',
      route: data.route || '',
    });
    
    console.log('📊 Logged notification_open to Firebase Analytics', {
      campaign_id: data.campaign_id,
      campaign_name: data.campaign_name,
    });
  }

  handleNotificationNavigation(remoteMessage) {
    // Add your navigation logic here
    const { data } = remoteMessage;
    
    if (data.screen) {
      // Example: navigate(data.screen, { route: data.route });
      console.log('Navigate to:', data.screen, data.route);
    }
  }

  setupNotificationHandlers() {
    // Foreground messages (app is open)
    messaging().onMessage(async (remoteMessage) => {
      console.log('Foreground notification:', remoteMessage);
      // Show local notification or in-app alert
    });

    // Background/Quit - Notification opened app
    messaging().onNotificationOpenedApp(async (remoteMessage) => {
      console.log('Notification opened app from background:', remoteMessage);
      
      // CRITICAL: Log to Analytics
      await this.logNotificationOpen(remoteMessage);
      
      // Handle navigation
      this.handleNotificationNavigation(remoteMessage);
    });

    // App was completely quit
    messaging()
      .getInitialNotification()
      .then(async (remoteMessage) => {
        if (remoteMessage) {
          console.log('Notification opened app from quit state:', remoteMessage);
          
          // CRITICAL: Log to Analytics
          await this.logNotificationOpen(remoteMessage);
          
          // Handle navigation
          this.handleNotificationNavigation(remoteMessage);
        }
      });
  }

  async requestPermission() {
    const authStatus = await messaging().requestPermission();
    const enabled =
      authStatus === messaging.AuthorizationStatus.AUTHORIZED ||
      authStatus === messaging.AuthorizationStatus.PROVISIONAL;

    if (enabled) {
      console.log('Authorization status:', authStatus);
      return true;
    }
    return false;
  }

  async getToken() {
    const token = await messaging().getToken();
    console.log('FCM Token:', token);
    return token;
  }
}

export default new NotificationService();
```

---

**In your `App.js`, import and initialize:**

```javascript
import NotificationService from './src/services/NotificationService';

function App() {
  useEffect(() => {
    // Request notification permissions
    NotificationService.requestPermission();
    
    // Get FCM token (save this to Firestore)
    NotificationService.getToken().then(token => {
      // Save to your backend/Firestore
      console.log('FCM Token:', token);
    });
  }, []);

  return (
    // ... your app content
  );
}
```

---

## 🧪 Step 3: Test the Implementation

1. **Deploy the updated app** to a test device
2. **Send a test notification** from your Notification Sender tool
3. **Click the notification** on the device
4. **Check logs** - you should see: `📊 Logged notification_open to Firebase Analytics`
5. **Wait 24-48 hours** for BigQuery to sync
6. **Run your BigQuery query**:

```sql
SELECT
  event_timestamp,
  event_name,
  event_params.key,
  event_params.value.string_value,
  user_pseudo_id
FROM
  `your-project.analytics_YOUR_APP_ID.events_*`
WHERE
  event_name = 'notification_open'
  AND _TABLE_SUFFIX BETWEEN FORMAT_DATE('%Y%m%d', DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY))
  AND FORMAT_DATE('%Y%m%d', CURRENT_DATE())
ORDER BY
  event_timestamp DESC
LIMIT 100
```

You should now see `message_id`, `message_name`, `campaign_id`, and `campaign_name` populated! ✅

---

## 📊 What Data Gets Tracked

When you send a notification from the tool with:
- **Campaign Name**: "November Property Launch"
- **Cohort**: "Premium Agents"

After implementing this code, BigQuery will show:

| Parameter | Value |
|-----------|-------|
| `message_id` | `campaign_20251106_143022` |
| `message_name` | `November Property Launch` |
| `campaign_id` | `campaign_20251106_143022` |
| `campaign_name` | `November Property Launch` |
| `cohort_tags` | `Premium Agents` |
| `screen` | `home` |
| `route` | `/` |

---

## ⚠️ Important Notes

1. **This code is REQUIRED** - There's no workaround or tool-only solution
2. **Firebase Console works** because Google's demo apps have this code pre-built
3. **Data is in the notification** - Your tool sends it correctly
4. **App must log it** - Only app code can write to Firebase Analytics
5. **Wait for sync** - BigQuery updates can take 24-48 hours

---

## 🔍 Debugging

If tracking still doesn't work:

```javascript
// Add this debug logging to your logNotificationOpen function:
async logNotificationOpen(remoteMessage) {
  console.log('🔍 Full notification data:', JSON.stringify(remoteMessage, null, 2));
  
  const { data } = remoteMessage;
  
  const eventParams = {
    message_id: data.campaign_id || data.message_id || '',
    message_name: data.campaign_name || data.message_name || remoteMessage.notification?.title || '',
    campaign_id: data.campaign_id || '',
    campaign_name: data.campaign_name || '',
    cohort_tags: data.cohort_tags || '',
  };
  
  console.log('📊 Event params being logged:', eventParams);
  
  await analytics().logEvent('notification_open', eventParams);
  
  console.log('✅ Analytics event logged successfully');
}
```

Check the device logs to ensure:
- Notification data contains `campaign_id` and `campaign_name`
- Analytics event is being logged
- No errors during logging

---

## ✅ Verification Checklist

- [ ] Installed `@react-native-firebase/messaging` and `@react-native-firebase/analytics`
- [ ] Added `logNotificationOpen()` function
- [ ] Hooked up `onNotificationOpenedApp` listener
- [ ] Hooked up `getInitialNotification` check
- [ ] Deployed app update to test device
- [ ] Sent test notification from tool
- [ ] Clicked notification and saw log message
- [ ] Waited 24-48 hours
- [ ] Checked BigQuery - seeing data! 🎉

---

## 🎯 Summary

**Before this code:**
```
Tool sends notification → User clicks → ❌ Nothing logged to Analytics → NULL in BigQuery
```

**After this code:**
```
Tool sends notification → User clicks → ✅ App logs to Analytics → Data in BigQuery! 🎉
```

That's it! Once you add this code and deploy, your campaign tracking will work perfectly.
