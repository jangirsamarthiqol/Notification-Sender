# 🍎 iOS Notification Setup Guide

## Problem
iOS notifications are failing with APNs (Apple Push Notification service) errors.

## Why iOS is Different
- **Android:** Works with just Firebase Admin SDK
- **iOS:** Requires APNs authentication certificate/key uploaded to Firebase Console
- **Reason:** Apple requires authentication for push notifications

---

## ✅ Step-by-Step Fix

### Step 1: Get APNs Authentication Key from Apple

**Option A: APNs Authentication Key (.p8 file) - Recommended**

1. Go to [Apple Developer Portal](https://developer.apple.com/account/resources/authkeys/list)
2. Click the **+** button to create a new key
3. Enter a **Key Name** (e.g., "Firebase Push Notifications")
4. Check **Apple Push Notifications service (APNs)**
5. Click **Continue** → **Register**
6. **Download the .p8 file** (⚠️ You can only download it ONCE!)
7. Note your **Key ID** (10 characters)
8. Note your **Team ID** (found in top-right of Apple Developer portal)

**Option B: APNs Certificate (.p12 file) - Legacy**

1. Go to [Apple Developer Portal → Certificates](https://developer.apple.com/account/resources/certificates/list)
2. Click **+** to create new certificate
3. Select **Apple Push Notification service SSL**
4. Choose your **App ID**
5. Follow instructions to create Certificate Signing Request (CSR)
6. Download the certificate and export as **.p12** file with a password

---

### Step 2: Upload to Firebase Console

1. Go to [Firebase Console](https://console.firebase.google.com/)
2. Select your project
3. Click **⚙️ Settings** (gear icon) → **Project settings**
4. Click **Cloud Messaging** tab
5. Scroll down to **Apple app configuration**

**If using .p8 file (APNs Auth Key):**
- Click **Upload** under "APNs Authentication Key"
- Upload your **.p8 file**
- Enter your **Key ID** (10 characters)
- Enter your **Team ID** (10 characters)
- Click **Upload**

**If using .p12 file (APNs Certificate):**
- Click **Upload** under "APNs Certificates"
- Upload your **.p12 file**
- Enter the **password** you set
- Click **Upload**

---

### Step 3: Add iOS Bundle ID to .env File

Create or update your `.env` file in the project root:

```env
# ... your existing Firebase config ...

# iOS Configuration - REQUIRED for iOS notifications
IOS_BUNDLE_ID=com.yourcompany.yourapp
```

**How to find your Bundle ID:**
- In Xcode: Open project → Select target → General tab → Bundle Identifier
- In Firebase Console: Project Settings → Your Apps → iOS app
- Example: `com.acn.agents` or `com.mycompany.myapp`

---

### Step 4: Verify APNs Setup

After uploading your APNs credentials:

1. In Firebase Console → Cloud Messaging
2. Under "Apple app configuration", you should see:
   - ✅ **APNs Auth Key uploaded** or **APNs Certificate uploaded**
   - Your Key ID and Team ID (if using .p8)
   - Certificate expiration date (if using .p12)

---

### Step 5: Test iOS Notifications

1. **Restart your notification sender app** (to load new .env variables)
   ```powershell
   python -m streamlit run NotificationSender.py
   ```

2. **Send a test notification** to an iOS device
3. Check for errors - you should no longer see "auth error from APNs"

---

## 🔍 Troubleshooting

### Error: "auth error from APNs" or "APNs authentication failed"

**Causes:**
- ❌ No APNs key/certificate uploaded to Firebase Console
- ❌ APNs certificate expired
- ❌ Wrong Team ID or Key ID
- ❌ Bundle ID mismatch

**Solutions:**
1. Verify APNs credentials in Firebase Console → Cloud Messaging
2. Check certificate expiration date
3. Ensure Bundle ID in `.env` matches your iOS app
4. Re-upload APNs key/certificate if needed

---

### Error: "Invalid registration token" (iOS only)

**Causes:**
- ❌ FCM token was generated before APNs was configured
- ❌ App was uninstalled/reinstalled
- ❌ Token expired

**Solutions:**
1. **Regenerate FCM token** in your iOS app after APNs setup
2. Delete old tokens from Firestore
3. Get fresh tokens from devices

---

### Error: "apns-topic" or Bundle ID errors

**Causes:**
- ❌ `IOS_BUNDLE_ID` not set in `.env` file
- ❌ Bundle ID doesn't match iOS app

**Solutions:**
1. Add `IOS_BUNDLE_ID=com.yourcompany.yourapp` to `.env`
2. Verify it matches Xcode → Target → Bundle Identifier
3. Restart notification sender app

---

### Notifications work on Android but not iOS

**Likely causes:**
- ✅ Android doesn't need APNs (that's iOS-only)
- ❌ APNs not configured for iOS

**Solution:**
- Follow Steps 1-3 above to set up APNs for iOS

---

## 📱 iOS App Requirements

Your React Native/iOS app also needs proper configuration:

### 1. Add Push Notification Capability
In Xcode:
1. Select your target
2. **Signing & Capabilities** tab
3. Click **+ Capability**
4. Add **Push Notifications**
5. Add **Background Modes** → Check "Remote notifications"

### 2. Request Notification Permissions

```javascript
import messaging from '@react-native-firebase/messaging';

async function requestUserPermission() {
  const authStatus = await messaging().requestPermission();
  const enabled =
    authStatus === messaging.AuthorizationStatus.AUTHORIZED ||
    authStatus === messaging.AuthorizationStatus.PROVISIONAL;

  if (enabled) {
    console.log('Authorization status:', authStatus);
    getFCMToken();
  }
}

async function getFCMToken() {
  const token = await messaging().getToken();
  console.log('FCM Token:', token);
  // Save this token to Firestore
}
```

### 3. Handle Notifications

```javascript
// Foreground notifications
messaging().onMessage(async remoteMessage => {
  console.log('Notification received in foreground:', remoteMessage);
});

// Background/Quit notifications
messaging().setBackgroundMessageHandler(async remoteMessage => {
  console.log('Background notification:', remoteMessage);
});
```

---

## ✅ Verification Checklist

Before sending iOS notifications, ensure:

- [ ] APNs Auth Key (.p8) or Certificate (.p12) uploaded to Firebase Console
- [ ] Key ID and Team ID entered correctly (if using .p8)
- [ ] Certificate not expired (if using .p12)
- [ ] `IOS_BUNDLE_ID` set in `.env` file
- [ ] Bundle ID matches iOS app in Xcode
- [ ] iOS app has Push Notifications capability enabled
- [ ] iOS app requests notification permissions
- [ ] Fresh FCM token generated after APNs setup
- [ ] Token saved to Firestore with `fsmToken` field

---

## 🎯 Quick Reference

| Component | What You Need |
|-----------|---------------|
| **Apple Developer** | APNs Key (.p8) or Certificate (.p12) |
| **Firebase Console** | Upload APNs credentials under Cloud Messaging |
| **Notification Tool** | Set `IOS_BUNDLE_ID` in `.env` file |
| **iOS App (Xcode)** | Enable Push Notifications capability |
| **iOS App (Code)** | Request permissions, get FCM token |

---

## 🆘 Still Having Issues?

If iOS notifications still fail after following this guide:

1. **Check Firebase Console Logs:**
   - Firebase Console → Cloud Messaging → View logs
   - Look for APNs delivery errors

2. **Verify Token Type:**
   - iOS tokens are longer than Android tokens
   - Make sure token type is detected as "ios"

3. **Test with Firebase Console:**
   - Firebase Console → Cloud Messaging → Send test message
   - If Firebase Console also fails → APNs setup issue
   - If Firebase Console works → Check your tool configuration

4. **Check App Logs:**
   - In Xcode, check device console logs
   - Look for FCM token registration errors
   - Verify notification permissions granted

---

## 📚 Additional Resources

- [Firebase iOS Setup Guide](https://firebase.google.com/docs/cloud-messaging/ios/client)
- [Apple APNs Overview](https://developer.apple.com/documentation/usernotifications)
- [React Native Firebase Messaging](https://rnfirebase.io/messaging/usage)
- [APNs Key vs Certificate Comparison](https://firebase.google.com/docs/cloud-messaging/ios/certs)

---

**After completing this setup, iOS notifications should work! 🎉**
