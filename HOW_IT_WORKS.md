# 🔥 How the Notification Sender Works

## 📊 Overview - Data Flow

```
Firebase Firestore → Fetch Tokens → Send via FCM → Devices
      ↓                  ↓              ↓            ↓
  acnAgents         Tool fetches    Firebase     iOS/Android
  collection        fsmToken       Cloud         App receives
                    field          Messaging     notification
```

---

## 🗄️ Firebase Data Structure

### Firestore Collection: `acnAgents`

Each document contains:
```javascript
{
  cpId: "CP001",           // Channel Partner ID (string)
  fsmToken: "eXaMpLeToK..."  // FCM token (string or array)
  // ... other fields
}
```

**Token formats:**
- **Single token:** `fsmToken: "eXaMpLeToKeN123..."`
- **Multiple tokens:** `fsmToken: ["token1...", "token2...", "token3..."]`

---

## 🔍 How Data is Fetched from Firebase

### Method 1: Fetch by CP IDs (Cohorts)
**Function:** `fetch_tokens_for_cpids(cpids)`

```python
def fetch_tokens_for_cpids(cpids):
    tokens = []
    # Split into chunks of 10 (Firestore 'in' query limit)
    for chunk in chunk_list(cpids, 10):
        query = db.collection('acnAgents').where('cpId', 'in', chunk)
        for doc in query.stream():
            data = doc.to_dict()
            raw = data.get('fsmToken')
            # Extract tokens...
    return tokens
```

**How it works:**
1. Takes list of CP IDs: `["CP001", "CP002", "CP003", ...]`
2. **Splits into chunks of 10** (Firestore limit for `in` queries)
3. For each chunk: `WHERE cpId IN ["CP001", "CP002", ..., "CP010"]`
4. Fetches matching documents
5. Extracts `fsmToken` from each document
6. Returns list of tokens with metadata

**Example:**
- Input: 50 CP IDs
- Queries: 5 queries (50 ÷ 10 = 5)
- Output: All tokens for those 50 agents

---

### Method 2: Fetch All Tokens (All Users)
**Function:** `fetch_all_tokens_directly()`

```python
@st.cache_data(ttl=300)  # Cached for 5 minutes
def fetch_all_tokens_directly():
    docs = list(db.collection('acnAgents').stream())
    total_docs = len(docs)
    
    for i, doc in enumerate(docs):
        data = doc.to_dict()
        raw = data.get('fsmToken')
        # Extract tokens...
        # Show progress: 100/1000, 200/1000, etc.
    
    return tokens
```

**How it works:**
1. **Single query:** Get ALL documents from `acnAgents`
2. Streams documents one by one
3. Extracts tokens from each
4. Shows progress bar: "📊 Processing 500/1000 agents..."
5. **Caches result for 5 minutes** (faster repeated access)

**Cached behavior:**
- First fetch: 10-30 seconds (depending on collection size)
- Next 5 minutes: Instant (returns cached data)
- After 5 minutes: Refetches fresh data

---

## 📏 Firebase Data Limits

### Firestore Read Limits

| Limit Type | Value | Impact |
|------------|-------|--------|
| **Free Tier Daily Reads** | 50,000 reads/day | Each document = 1 read |
| **Paid Tier** | Unlimited (charged per read) | $0.06 per 100,000 reads |
| **Single Query Max** | 1 MB response size | ~1,000-5,000 docs depending on size |
| **`in` Query Limit** | 10 values max | Why we chunk CP IDs into groups of 10 |
| **Concurrent Connections** | No hard limit | Firebase auto-scales |

### Your Collection Size

**To check your current usage:**
```python
# In Python
docs = list(db.collection('acnAgents').stream())
print(f"Total agents: {len(docs)}")
```

**Example calculations:**

| Agents | Reads per "Fetch All" | Daily Limit (Free) | Daily Limit (Paid) |
|--------|----------------------|-------------------|-------------------|
| 100 | 100 reads | 500 fetches/day | Unlimited |
| 1,000 | 1,000 reads | 50 fetches/day | Unlimited |
| 10,000 | 10,000 reads | 5 fetches/day | Unlimited |
| 50,000 | 50,000 reads | 1 fetch/day | Unlimited |

**Note:** The 5-minute cache helps reduce reads significantly!

---

## 🚀 Token Detection Logic

### How Platform is Detected

```python
def detect_token_type(token):
    # Modern FCM tokens work for both iOS & Android
    # Detection is best-effort based on patterns
    
    if token.startswith(('APA91b', 'AAAA')):
        return "android"  # Legacy Android pattern
    elif token.startswith(('f', 'd', 'e', 'c')):
        return "ios"  # Common iOS patterns
    elif len(token) > 140:
        return "fcm"  # Universal token (most common now)
    else:
        return "android"  # Default fallback
```

**Why detection isn't perfect:**
- Modern FCM tokens are **platform-agnostic**
- Same token format works for both iOS and Android
- Platform determined by **which app registered the token**
- Tool uses heuristics for backward compatibility

**Impact:**
- If detected wrong, notification still sends (FCM handles it)
- Affects which config is used (Android vs iOS)
- Both configs are provided, so FCM picks the right one

---

## 📤 How Notifications are Sent

### Parallel Processing

```python
def send_notifications_parallel(title, body, tokens, 
                                batch_size=100, 
                                max_workers=10):
    # Split tokens into batches of 100
    # Process 10 batches simultaneously
    # Each batch sends 100 notifications
```

**Process:**
1. Split tokens into **batches of 100**
2. Use **10 worker threads** to process batches in parallel
3. Each thread sends notifications sequentially within its batch
4. Progress updates in real-time

**Performance:**
- **1,000 tokens:** ~1-2 minutes
- **10,000 tokens:** ~10-15 minutes
- **100,000 tokens:** ~90-120 minutes

### FCM Message Structure

```python
message = messaging.Message(
    token="user_device_token",
    notification=messaging.Notification(
        title="Your Title",
        body="Your Message"
    ),
    data={
        "campaign_id": "campaign_20251121_143022",
        "campaign_name": "Weekend Sale",
        "screen": "home",
        "route": "/"
    },
    android=android_config,  # Android-specific settings
    # OR
    apns=apns_config  # iOS-specific settings
)

response = messaging.send(message)  # Returns message ID
```

---

## 💰 Firebase Costs

### Free Tier (Spark Plan)
- **Firestore Reads:** 50,000/day
- **FCM Messages:** Unlimited
- **Storage:** 1 GB

### Paid Tier (Blaze Plan)
- **Firestore Reads:** $0.06 per 100,000 reads
- **FCM Messages:** Free (unlimited)
- **Storage:** $0.18/GB

**Example monthly cost for 10,000 agents:**

| Action | Frequency | Reads | Cost/month |
|--------|-----------|-------|-----------|
| Fetch all daily | 30 times | 300,000 | $0.18 |
| Cohort fetches | 100 times | 10,000 | $0.01 |
| **Total** | | **310,000** | **$0.19/month** |

**FCM is completely free!** No cost per notification.

---

## 🎯 Optimization Tips

### 1. Use Cohorts Instead of "All Users"
- **Cohort fetch:** Only reads needed documents
- **All users:** Reads entire collection
- **Savings:** 10x-100x fewer reads

### 2. Leverage the Cache
```python
@st.cache_data(ttl=300)  # Caches for 5 minutes
```
- First fetch: Reads from Firebase
- Next 5 minutes: Returns cached data (0 reads)
- **Best practice:** Fetch once, send multiple campaigns

### 3. Batch Operations
- Current: Processes 100 tokens per batch
- Can increase to 500 if Firebase allows
- Faster but uses more memory

### 4. Schedule Off-Peak
- Fetch tokens during low-traffic hours
- Send notifications when needed
- Reduces concurrent load

---

## 📊 Data Flow Summary

```
1. USER SELECTS COHORTS
   ↓
2. TOOL QUERIES FIRESTORE
   db.collection('acnAgents').where('cpId', 'in', ['CP001', 'CP002', ...])
   ↓
3. FIRESTORE RETURNS DOCUMENTS
   {cpId: "CP001", fsmToken: "abc123..."}
   {cpId: "CP002", fsmToken: ["xyz456...", "def789..."]}
   ↓
4. TOOL EXTRACTS TOKENS
   ["abc123...", "xyz456...", "def789..."]
   ↓
5. TOOL DETECTS PLATFORM
   "abc123..." → Android/iOS/FCM
   ↓
6. TOOL SENDS VIA FCM
   messaging.send(Message(...))
   ↓
7. FCM DELIVERS TO DEVICES
   Android → Google Play Services
   iOS → APNs → Device
```

---

## 🔢 Real Numbers - Your Collection

**To get actual data about your collection, run:**

1. **Count total agents:**
```python
docs = list(db.collection('acnAgents').stream())
print(f"Total agents: {len(docs)}")
```

2. **Count tokens:**
```python
token_count = 0
for doc in docs:
    token = doc.to_dict().get('fsmToken')
    if isinstance(token, str):
        token_count += 1
    elif isinstance(token, list):
        token_count += len(token)
print(f"Total tokens: {token_count}")
```

3. **Check read usage (Firebase Console):**
- Firebase Console → Firestore → Usage tab
- Shows: Reads today, reads this month
- Compare to limits

---

## ❓ FAQ

**Q: What happens if I fetch all tokens multiple times?**
A: First time reads from Firebase. Next 5 minutes uses cache (free).

**Q: Can I exceed the 50,000 daily read limit?**
A: Yes, but you need to upgrade to Blaze (pay-as-you-go) plan.

**Q: How many notifications can I send per day?**
A: Unlimited! FCM has no hard limit. Typical: millions/day.

**Q: What if a token is invalid?**
A: Firebase returns an error. Tool marks it as failed but doesn't delete it.

**Q: Can I fetch 100,000 agents?**
A: Yes, but it will take 100,000 reads. May hit daily limit on free tier.

---

Need more specific info about your collection size or costs? Let me know! 📊
