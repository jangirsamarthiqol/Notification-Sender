import os
import streamlit as st
import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore, messaging
from firebase_admin.exceptions import FirebaseError
import time
from typing import List, Union
import sys

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
@st.cache_resource(ttl=3600)  # Cache for 1 hour
def init_firebase():
    try:
        if not firebase_admin._apps:
            # Handle private key formatting
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
        st.error(f"Failed to initialize Firebase: {str(e)}")
        st.stop()

try:
    db = init_firebase()
except Exception as e:
    st.error(f"Failed to connect to Firebase: {str(e)}")
    st.stop()

# --- Helper functions ---

def chunk_list(lst, n):
    """Yield successive n-sized chunks from list."""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]

def fetch_tokens_for_cpids(cpids):
    """
    Fetch tokens for a list of cpIds.
    Firestore allows max 10 in queries, so we batch the cpIds accordingly.
    Returns list of tuples (doc_ref, token, is_array)
    """
    tokens = []
    for chunk in chunk_list(cpids, 10):
        query = db.collection('agents').where('cpId', 'in', chunk)
        docs = query.stream()
        for doc in docs:
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
    """
    Fetch all cpIds from the agents collection.
    Returns a list of unique cpIds.
    """
    cpids = set()
    docs = db.collection('agents').stream()
    for doc in docs:
        data = doc.to_dict()
        if 'cpId' in data and data['cpId']:
            cpids.add(str(data['cpId']))
    return list(cpids)

def send_notifications(title, body, tokens, batch_size=100):
    """
    Send notifications in batches.
    Returns summary dict with counts.
    """
    summary = {"success": 0, "pruned": 0, "errors": 0}
    errors_list = []

    for i in range(0, len(tokens), batch_size):
        batch = tokens[i:i+batch_size]
        for doc_ref, token, is_array in batch:
            message = messaging.Message(
                token=token,
                notification=messaging.Notification(title=title, body=body)
            )
            try:
                messaging.send(message)
                summary["success"] += 1
                st.write(f"✅ Sent to token: {token[:8]}...")
            except FirebaseError as e:
                code = getattr(e, 'code', '')
                if 'registration-token' in code or code == 'NOT_FOUND':
                    # Prune token
                    if is_array:
                        doc_ref.update({"fsmToken": firestore.ArrayRemove([token])})
                        st.write(f"🗑 Removed expired token {token[:8]}... from array.")
                    else:
                        doc_ref.update({"fsmToken": firestore.DELETE_FIELD})
                        st.write(f"🗑 Deleted fsmToken field (single token).")
                    summary["pruned"] += 1
                else:
                    st.warning(f"⚠️ FCM error for {token[:8]}...: {e}")
                    errors_list.append((token, str(e)))
                    summary["errors"] += 1
            except Exception as e:
                st.error(f"❌ Unexpected error for {token[:8]}...: {e}")
                errors_list.append((token, str(e)))
                summary["errors"] += 1
        # Avoid throttling / UI freezing
        time.sleep(0.1)
    return summary, errors_list


# --- Streamlit UI ---

st.title("ACN Agent FCM Notification Sender")

st.markdown("""
Upload a CSV containing the `cpId` of agents you want to notify, or manually enter cpIds below.
Enter the notification **Title** and **Body** below.
Invalid or expired tokens will be pruned automatically.
""")

send_to_all = st.checkbox("Send to all agents", help="If checked, will send notifications to all agents in the database")

if not send_to_all:
    uploaded_file = st.file_uploader("Upload CSV file with 'cpId' column", type=["csv"])
    manual_cpids = st.text_area("Or manually enter cpIds (one per line)", help="Enter each cpId on a new line")

title = st.text_input("Notification Title").strip()
body = st.text_area("Notification Body").strip()

send_button = st.button("Send Notifications")

if send_button:
    cpids = []
    
    if send_to_all:
        st.info("Fetching all cpIds from database...")
        cpids = fetch_all_cpids()
        st.success(f"Found {len(cpids)} unique cpIds in the database.")
    else:
        # Get cpIds from CSV if uploaded
        if uploaded_file:
            try:
                df = pd.read_csv(uploaded_file)
                if 'cpId' not in df.columns:
                    st.error("CSV must contain 'cpId' column.")
                else:
                    cpids.extend(df['cpId'].dropna().astype(str).unique().tolist())
            except Exception as e:
                st.error(f"Failed to read CSV: {e}")
        
        # Get cpIds from manual input
        if manual_cpids:
            manual_ids = [id.strip() for id in manual_cpids.split('\n') if id.strip()]
            cpids.extend(manual_ids)
    
    if not cpids:
        st.error("Please provide cpIds either through CSV upload or manual input.")
    elif not title:
        st.error("Notification title cannot be empty.")
    elif not body:
        st.error("Notification body cannot be empty.")
    else:
        # Remove duplicates and empty values
        cpids = list(set(filter(None, cpids)))
        st.info(f"Fetching tokens for {len(cpids)} unique cpIds...")
        tokens = fetch_tokens_for_cpids(cpids)
        st.success(f"Found {len(tokens)} tokens associated with provided cpIds.")

        if not tokens:
            st.warning("No tokens found for given cpIds. Nothing to send.")
        else:
            summary, errors = send_notifications(title, body, tokens)
            st.write("---")
            st.success(f"✅ Notifications sent successfully: {summary['success']}")
            st.warning(f"🗑 Tokens pruned (removed): {summary['pruned']}")
            if summary['errors']:
                st.error(f"❌ Errors encountered for {summary['errors']} tokens.")
                for tkn, err in errors:
                    st.write(f"- Token {tkn[:8]}...: {err}")
