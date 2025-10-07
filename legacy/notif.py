import os
import streamlit as st

# Set page config first, before any other Streamlit commands
st.set_page_config(
    page_title="ACN FCM Notification Sender (No Media)",
    page_icon="📱",
    layout="wide"
)

import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore, messaging
from firebase_admin.exceptions import FirebaseError
import time
from typing import List, Union
import io
import csv

# Check required environment variables
required_env_vars = [
    "FIREBASE_TYPE",
    "FIREBASE_PROJECT_ID",
    "FIREBASE_PRIVATE_KEY_ID",
    "FIREBASE_PRIVATE_KEY",
    "FIREBASE_CLIENT_EMAIL",
    "FIREBASE_CLIENT_ID",
    "FIREBASE_AUTH_URI",
    "FIREBASE_TOKEN_URI",
    "FIREBASE_AUTH_PROVIDER_CERT_URL",
    "FIREBASE_CLIENT_CERT_URL"
]

missing_vars = [var for var in required_env_vars if not os.getenv(var)]
if missing_vars:
    st.error(f"Missing required environment variables: {', '.join(missing_vars)}")
    st.stop()

# --- Firebase init via env vars (run once) ---
@st.cache_resource(ttl=3600)
def init_firebase():
    try:
        if not firebase_admin._apps:
            private_key = os.getenv("FIREBASE_PRIVATE_KEY")
            if private_key:
                private_key = private_key.replace("\\n", "\n")
            cred_info = {
                "type": os.getenv("FIREBASE_TYPE"),
                "project_id": os.getenv("FIREBASE_PROJECT_ID"),
                "private_key_id": os.getenv("FIREBASE_PRIVATE_KEY_ID"),
                "private_key": private_key,
                "client_email": os.getenv("FIREBASE_CLIENT_EMAIL"),
                "client_id": os.getenv("FIREBASE_CLIENT_ID"),
                "auth_uri": os.getenv("FIREBASE_AUTH_URI"),
                "token_uri": os.getenv("FIREBASE_TOKEN_URI"),
                "auth_provider_x509_cert_url": os.getenv("FIREBASE_AUTH_PROVIDER_CERT_URL"),
                "client_x509_cert_url": os.getenv("FIREBASE_CLIENT_CERT_URL"),
            }
            cred = credentials.Certificate(cred_info)
            firebase_admin.initialize_app(cred)
        return firestore.client()
    except Exception as e:
        st.error(f"Failed to initialize Firebase: {e}")
        st.stop()

try:
    db = init_firebase()
except Exception as e:
    st.error(f"Failed to connect to Firebase: {e}")
    st.stop()

# --- Helper functions ---
def chunk_list(lst, n):
    """Yield successive n-sized chunks from list."""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]

def fetch_tokens_for_cpids(cpids):
    """
    Fetch tokens for a list of cpIds.
    Returns list of tuples (doc_ref, token, is_array).
    """
    tokens = []
    for chunk in chunk_list(cpids, 10):
        query = db.collection('acnAgents').where('cpId', 'in', chunk)
        for doc in query.stream():
            data = doc.to_dict()
            raw = data.get('fsmToken')
            doc_ref = doc.reference
            if isinstance(raw, str) and raw.strip():
                tokens.append((doc_ref, raw.strip(), False))
            elif isinstance(raw, (list, tuple)):
                for t in raw:
                    if isinstance(t, str) and t.strip():
                        tokens.append((doc_ref, t.strip(), True))
    return tokens

def fetch_all_cpids():
    """Fetch all cpIds from the acnAgents collection."""
    cpids = set()
    for doc in db.collection('acnAgents').stream():
        data = doc.to_dict()
        if data.get('cpId'):
            cpids.add(str(data['cpId']))
    return list(cpids)

def send_notifications(title, body, tokens, batch_size=100):
    """
    Send notifications (no media, default sound only).
    Prune tokens on SenderId mismatch and APNS/Web Push auth errors too.
    """
    summary = {"success": 0, "pruned": 0, "errors": 0}
    errors_list = []

    for i in range(0, len(tokens), batch_size):
        batch = tokens[i:i + batch_size]
        for doc_ref, token, is_array in batch:
            try:
                # 1) Build standard payloads
                ios_sound = "default"
                android_sound = "default"
                data_payload = {
                    "title": title,
                    "body": body,
                    "click_action": "FLUTTER_NOTIFICATION_CLICK",
                    "type": "rich_notification"
                }

                # 2) Notification object
                notification = messaging.Notification(title=title, body=body)

                # 3) Android config
                android_notification = messaging.AndroidNotification(
                    title=title,
                    body=body,
                    sound=android_sound,
                    channel_id="high_importance_channel",
                    priority="high",
                    visibility="public",
                    click_action="FLUTTER_NOTIFICATION_CLICK"
                )
                android_config = messaging.AndroidConfig(
                    notification=android_notification,
                    priority="high",
                    ttl=3600,
                    data=data_payload,
                    collapse_key="rich_notification"
                )

                # 4) iOS/APNS config
                aps_alert = messaging.ApsAlert(title=title, body=body)
                aps = messaging.Aps(
                    alert=aps_alert,
                    sound=ios_sound,
                    badge=1,
                    mutable_content=True,
                    content_available=True,
                    category="RICH_NOTIFICATION"
                )
                apns_payload = messaging.APNSPayload(aps=aps)
                apns_payload.custom_data = {
                    "notification": {"title": title, "body": body},
                    "data": data_payload
                }
                apns_config = messaging.APNSConfig(
                    payload=apns_payload,
                    headers={
                        "apns-priority": "10",
                        "apns-push-type": "alert",
                        "apns-expiration": "0",
                        "apns-collapse-id": "rich_notification",
                        "apns-content-available": "1"
                    }
                )

                # 5) Send the message
                message = messaging.Message(
                    token=token,
                    notification=notification,
                    android=android_config,
                    apns=apns_config,
                    data=data_payload
                )
                response = messaging.send(message)
                st.write(f"📱 Sent to {token[:8]}… (ID: {response})")
                summary["success"] += 1

            except FirebaseError as e:
                code = getattr(e, 'code', '')
                msg = str(e).lower()
                # Prune-worthy conditions
                prune_conditions = [
                    'registration-token' in code,
                    code == 'not_found',
                    'invalid-registration-token' in msg,
                    'unregistered' in msg,
                    'senderid mismatch' in msg,
                    'auth error from apns' in msg,
                    'web push service' in msg
                ]
                if any(prune_conditions):
                    try:
                        if is_array:
                            doc_ref.update({"fsmToken": firestore.ArrayRemove([token])})
                            st.write(f"🗑️ Removed expired token {token[:8]}…")
                        else:
                            doc_ref.update({"fsmToken": firestore.DELETE_FIELD})
                            st.write(f"🗑️ Deleted fsmToken field for {token[:8]}…")
                        summary["pruned"] += 1
                    except Exception as prune_err:
                        st.warning(f"⚠️ Could not prune {token[:8]}…: {prune_err}")
                        summary["errors"] += 1
                else:
                    st.warning(f"⚠️ FCM error for {token[:8]}…: {e}")
                    errors_list.append((token, str(e)))
                    summary["errors"] += 1

            except Exception as e:
                st.error(f"❌ Unexpected error for {token[:8]}…: {e}")
                errors_list.append((token, str(e)))
                summary["errors"] += 1

        time.sleep(0.2)

    return summary, errors_list


def validate_token(token):
    """Validate actual FCM token format (not for cpIds!)."""
    if not isinstance(token, str):
        return False
    token = token.strip()
    return 10 < len(token) < 4096 and ' ' not in token

def send_test_notification(test_token, title, body):
    """Send a single test notification."""
    try:
        if not validate_token(test_token):
            return False, "Invalid token format."
        data_payload = {
            "title": title,
            "body": body,
            "click_action": "FLUTTER_NOTIFICATION_CLICK",
            "type": "test_notification"
        }
        notification = messaging.Notification(title=title, body=body)
        android_config = messaging.AndroidConfig(
            notification=messaging.AndroidNotification(
                title=title,
                body=body,
                sound="default",
                channel_id="high_importance_channel",
                priority="high",
                visibility="public",
                click_action="FLUTTER_NOTIFICATION_CLICK"
            ),
            priority="high",
            ttl=3600,
            data=data_payload,
            collapse_key="test_notification"
        )
        aps_alert = messaging.ApsAlert(title=title, body=body)
        aps = messaging.Aps(
            alert=aps_alert,
            sound="default",
            badge=1,
            mutable_content=True,
            content_available=True,
            category="TEST_NOTIFICATION"
        )
        apns_payload = messaging.APNSPayload(aps=aps)
        apns_payload.custom_data = {
            "notification": {"title": title, "body": body},
            "data": {"title": title, "body": body, "type": "test_notification"}
        }
        apns_config = messaging.APNSConfig(
            payload=apns_payload,
            headers={
                "apns-priority": "10",
                "apns-push-type": "alert",
                "apns-expiration": "0",
                "apns-collapse-id": "test_notification",
                "apns-content-available": "1"
            }
        )
        message = messaging.Message(
            token=test_token,
            notification=notification,
            android=android_config,
            apns=apns_config,
            data=data_payload
        )
        response = messaging.send(message)
        return True, f"Test notification sent! (ID: {response})"
    except Exception as e:
        return False, str(e)

# --- Streamlit UI ---
st.title("📱 ACN Agent FCM Notification Sender (No Media)")
st.markdown("""
Send notifications (no media) to your agents. Upload CSV files, enter cpIds manually, or send to all agents. Invalid tokens are automatically pruned.
""")

batch_size = 100
tab1, tab2 = st.tabs(["📝 Compose", "📊 Recipients"])

with tab2:
    st.subheader("📊 Select Recipients")
    send_to_all = st.checkbox("📢 Send to all agents")
    if send_to_all:
        st.info("📋 Will fetch all agent cpIds from the database")
    else:
        col1, col2 = st.columns(2)
        with col1:
            st.write("**📁 Upload CSV File**")
            uploaded_csv = st.file_uploader("Select CSV file with cpId column", type="csv")
            if uploaded_csv:
                try:
                    df = pd.read_csv(uploaded_csv)
                    st.dataframe(df.head())
                    if 'cpId' in df.columns:
                        st.success(f"✅ Found {df['cpId'].dropna().shape[0]} cpIds")
                    else:
                        st.error("❌ CSV must contain a 'cpId' column")
                except Exception as e:
                    st.error(f"❌ Error reading CSV: {e}")
        with col2:
            st.write("**✏️ Manual Entry**")
            manual_cpids = st.text_area("Enter cpIds (one per line)", height=150)
            if manual_cpids:
                lines = [c.strip() for c in manual_cpids.split('\n') if c.strip()]
                st.info(f"📝 {len(lines)} cpIds entered")

with tab1:
    st.subheader("📝 Notification Content")
    title = st.text_input("📌 Notification Title", max_chars=100).strip()
    body = st.text_area("📄 Notification Body", height=100, max_chars=500).strip()
    if title or body:
        st.subheader("👀 Preview")
        st.markdown(f"""
<div style="border:1px solid #ddd;border-radius:10px;padding:15px;background:#f9f9f9;">
  <h4 style="margin:0 0 10px;">{title or 'Title'}</h4>
  <p style="margin:0;">{body or 'Notification body will appear here...'}</p>
</div>
""", unsafe_allow_html=True)

    st.markdown("---")
    st.subheader("🧪 Test Notification")
    test_token = st.text_input("Enter a single FCM token to test")
    if st.button("Send Test Notification"):
        if not test_token:
            st.warning("Please enter a test token.")
        elif not (title and body):
            st.warning("Enter both title and body first.")
        else:
            ok, msg = send_test_notification(test_token, title, body)
            st.success(msg) if ok else st.error(f"Failed: {msg}")

col1, col2, col3 = st.columns([1,2,1])
with col2:
    send_button = st.button("🚀 Send Notifications", use_container_width=True)

if send_button:
    if not title:
        st.error("❌ Title cannot be empty.")
        st.stop()
    if not body:
        st.error("❌ Body cannot be empty.")
        st.stop()

    # gather cpIds
    cpids = []
    if send_to_all:
        cpids = fetch_all_cpids()
        st.success(f"✅ Found {len(cpids)} cpIds in DB")
    else:
        if 'uploaded_csv' in locals() and uploaded_csv:
            df = pd.read_csv(uploaded_csv)
            cpids += df['cpId'].dropna().astype(str).tolist() if 'cpId' in df.columns else []
        if 'manual_cpids' in locals() and manual_cpids:
            cpids += [c for c in manual_cpids.split('\n') if c.strip()]

    cpids = list(set(filter(None, cpids)))
    st.info(f"📝 Processing {len(cpids)} unique cpIds...")
    if not cpids:
        st.error("❌ No cpIds provided after input cleaning.")
        st.stop()

    # fetch and validate actual FCM tokens
    with st.spinner("🔍 Fetching notification tokens..."):
        tokens = fetch_tokens_for_cpids(cpids)

    valid_tokens, invalid_tokens = [], []
    for doc_ref, token, is_array in tokens:
        if validate_token(token):
            valid_tokens.append((doc_ref, token, is_array))
        else:
            invalid_tokens.append(token)

    if invalid_tokens:
        st.warning(f"⚠️ {len(invalid_tokens)} invalid tokens will be skipped")
        with st.expander("View invalid tokens"):
            for t in invalid_tokens:
                st.write(t)

    tokens = valid_tokens
    if not tokens:
        st.warning("⚠️ No valid tokens found. Exiting.")
        st.stop()

    st.success(f"✅ Found {len(tokens)} valid tokens.")
    st.markdown("### 📤 Sending Notifications...")
    progress_bar = st.progress(0)
    status_text = st.empty()

    with st.spinner("🚀 Sending..."):
        summary, errors = send_notifications(title, body, tokens, batch_size)

    progress_bar.progress(100)
    status_text.text("✅ Done!")
    st.markdown("---")

    # summary
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("✅ Sent", summary['success'])
    c2.metric("🗑️ Pruned", summary['pruned'])
    c3.metric("❌ Errors", summary['errors'])
    rate = summary['success']/len(tokens)*100 if tokens else 0
    c4.metric("📈 Success %", f"{rate:.1f}%")

    if summary['errors']:
        with st.expander("View Errors"):
            for tok, err in errors:
                st.write(f"🔴 {tok[:8]}…: {err}")

st.markdown("""
---
<div style="text-align:center;color:#666;">
  📱 ACN Agent FCM Notification Sender | Streamlit & Firebase
</div>
""", unsafe_allow_html=True)
