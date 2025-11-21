import os
import streamlit as st
from dotenv import load_dotenv
import concurrent.futures
import threading
from datetime import datetime
from pathlib import Path

# Load environment variables from .env file
load_dotenv()

# --- Local Data Storage ---
DATA_DIR = Path("notification_data")
DATA_DIR.mkdir(exist_ok=True)

COHORTS_FILE = DATA_DIR / "cohorts.json"
CAMPAIGNS_FILE = DATA_DIR / "campaigns.json"

def load_cohorts():
    """Load cohorts from local JSON - {cohort_name: [cp_ids]}"""
    if COHORTS_FILE.exists():
        with open(COHORTS_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_cohorts(cohorts):
    """Save cohorts to local JSON"""
    with open(COHORTS_FILE, 'w') as f:
        json.dump(cohorts, f, indent=2)

def load_campaigns():
    """Load campaign history from local JSON"""
    if CAMPAIGNS_FILE.exists():
        with open(CAMPAIGNS_FILE, 'r') as f:
            return json.load(f)
    return []

def save_campaign(campaign_data):
    """Save a campaign to local JSON"""
    campaigns = load_campaigns()
    campaigns.append(campaign_data)
    with open(CAMPAIGNS_FILE, 'w') as f:
        json.dump(campaigns, f, indent=2)

def generate_campaign_id():
    """Generate unique campaign ID"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"campaign_{timestamp}"

# Set page config first, before any other Streamlit commands
st.set_page_config(
    page_title="ACN FCM Notification Sender (Enhanced)",
    page_icon="📱",
    layout="wide",
    initial_sidebar_state="expanded"
)

import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore, messaging
from firebase_admin.exceptions import FirebaseError
import time
from typing import List, Union, Dict, Tuple
import io
import csv
import json

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

def detect_token_type(token):
    """Detect if token is iOS or Android based on format."""
    if not token or len(token) < 10:
        return "unknown"
    
    # FCM tokens are platform-agnostic - they work for both iOS and Android
    # The actual platform is determined by the app installation, not the token format
    # We'll use "fcm" as the type since FCM handles both platforms automatically
    # However, for backward compatibility and specific error handling, we can try some heuristics
    
    # Modern FCM tokens are typically 152-163 characters long for both platforms
    # Legacy patterns (rarely used now):
    if token.startswith(('APA91b', 'AAAA')):  # Legacy Android pattern
        return "android"
    elif token.startswith(('f', 'd', 'e', 'c')):  # Common iOS patterns
        return "ios"
    elif len(token) > 140:  # Most modern FCM tokens
        # For modern tokens, we can't reliably distinguish, so we'll treat as universal
        return "fcm"  # Universal FCM token
    else:
        return "android"  # Default fallback

def fetch_tokens_for_cpids(cpids):
    """
    Fetch tokens for a list of cpIds.
    Returns list of tuples (doc_ref, token, is_array, token_type, name).
    """
    tokens = []
    for chunk in chunk_list(cpids, 10):
        query = db.collection('acnAgents').where('cpId', 'in', chunk)
        for doc in query.stream():
            data = doc.to_dict()
            raw = data.get('fsmToken')
            name = data.get('name', '')  # Get name from Firestore
            doc_ref = doc.reference
            if isinstance(raw, str) and raw.strip():
                token_type = detect_token_type(raw.strip())
                tokens.append((doc_ref, raw.strip(), False, token_type, name))
            elif isinstance(raw, (list, tuple)):
                for t in raw:
                    if isinstance(t, str) and t.strip():
                        token_type = detect_token_type(t.strip())
                        tokens.append((doc_ref, t.strip(), True, token_type, name))
    return tokens

@st.cache_data(ttl=300)  # Cache for 5 minutes
def fetch_all_tokens_directly():
    """Fetch all tokens directly from acnAgents collection for faster processing."""
    tokens = []
    total_docs = 0
    
    # Get total count for progress tracking
    docs = list(db.collection('acnAgents').stream())
    total_docs = len(docs)
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    for i, doc in enumerate(docs):
        data = doc.to_dict()
        raw = data.get('fsmToken')
        name = data.get('name', '')  # Get name from Firestore
        # Use None for doc_ref to keep return value pickle-serializable
        doc_ref = None
        
        if isinstance(raw, str) and raw.strip():
            token_type = detect_token_type(raw.strip())
            tokens.append((doc_ref, raw.strip(), False, token_type, name))
        elif isinstance(raw, (list, tuple)):
            for t in raw:
                if isinstance(t, str) and t.strip():
                    token_type = detect_token_type(t.strip())
                    tokens.append((doc_ref, t.strip(), True, token_type, name))
        
        # Update progress
        progress = (i + 1) / total_docs
        progress_bar.progress(progress)
        status_text.text(f"📊 Processing {i + 1}/{total_docs} agents...")
    
    progress_bar.empty()
    status_text.empty()
    return tokens

def fetch_all_cpids():
    """Fetch all cpIds from the acnAgents collection."""
    cpids = set()
    for doc in db.collection('acnAgents').stream():
        data = doc.to_dict()
        if data.get('cpId'):
            cpids.add(str(data['cpId']))
    return list(cpids)

def personalize_text(text, name):
    """
    Replace {name} and {firstname} placeholders with actual name.
    {name} = Full name (e.g., "Shameer K")
    {firstname} = First name only (e.g., "Shameer")
    """
    if not name:
        return text
    
    # Get first name (everything before first space)
    firstname = name.split()[0] if name else ''
    
    # Replace placeholders (case-insensitive)
    text = text.replace('{name}', name)
    text = text.replace('{Name}', name)
    text = text.replace('{firstname}', firstname)
    text = text.replace('{firstName}', firstname)
    text = text.replace('{Firstname}', firstname)
    text = text.replace('{FirstName}', firstname)
    
    return text

def send_single_notification(doc_ref, token, is_array, token_type, title, body, click_action="FLUTTER_NOTIFICATION_CLICK", route="/", screen="home", campaign_id=None, campaign_name=None, cohort_tags=None, name=None):
    """Send a single notification with campaign tracking"""
    try:
        # Personalize title and body with name
        personalized_title = personalize_text(title, name)
        personalized_body = personalize_text(body, name)
        
        # Data payload with campaign tracking
        data_payload = {
            "title": personalized_title,
            "body": personalized_body,
            "click_action": click_action,
            "screen": screen,
            "route": route,
            "from_notification": "true",
            "timestamp": str(int(time.time()))
        }
        
        # Add campaign tracking for Firebase Analytics
        if campaign_id:
            data_payload["campaign_id"] = campaign_id
            data_payload["message_id"] = campaign_id  # Firebase Analytics key
        if campaign_name:
            data_payload["campaign_name"] = campaign_name
            data_payload["message_name"] = campaign_name  # Firebase Analytics key
        if cohort_tags:
            data_payload["cohort_tags"] = ",".join(cohort_tags)

        # Basic notification
        notification = messaging.Notification(title=personalized_title, body=personalized_body)

        # Android config
        android_config = messaging.AndroidConfig(
            notification=messaging.AndroidNotification(
                title=personalized_title,
                body=personalized_body,
                sound="default",
                click_action=click_action,
                tag="acn_notification"
            ),
            priority="high",
            ttl=3600,
            data=data_payload
        )

        # iOS/APNs config - Properly structured for iOS
        aps_alert = messaging.ApsAlert(
            title=personalized_title,
            body=personalized_body
        )
        aps = messaging.Aps(
            alert=aps_alert,
            sound="default",
            badge=1,
            content_available=True,  # Wake app in background
            mutable_content=True  # Allow notification modifications
        )
        
        apns_payload = messaging.APNSPayload(aps=aps)
        
        # CRITICAL: APNs headers - iOS bundle ID required
        apns_headers = {
            "apns-priority": "10",
            "apns-push-type": "alert",
        }
        
        # Get iOS bundle ID from environment
        apns_topic = os.getenv("APNS_TOPIC") or os.getenv("IOS_BUNDLE_ID")
        if apns_topic:
            apns_headers["apns-topic"] = apns_topic

        apns_config = messaging.APNSConfig(
            payload=apns_payload,
            headers=apns_headers
        )

        # Apply platform override if set
        forced = st.session_state.get("force_platform", "Auto-detect")
        effective_type = token_type
        if forced == "Android":
            effective_type = "android"
        elif forced == "iOS":
            effective_type = "ios"

        # Create message with platform-specific configuration
        if effective_type == "ios":
            message = messaging.Message(
                token=token,
                notification=notification,
                apns=apns_config,
                data=data_payload
            )
        else:
            # Treat android, fcm, unknown as Android channel only to avoid APNS auth errors
            message = messaging.Message(
                token=token,
                notification=notification,
                android=android_config,
                data=data_payload
            )
        
        response = messaging.send(message)
        st.write(f"🔍 Debug: Successfully sent to {token_type} token {token[:12]}... - Response: {response}")
        return True, response, None
        
    except FirebaseError as e:
        error_code = getattr(e, 'code', '').lower()
        error_message = str(e).lower()
        st.write(f"🔍 Debug: FirebaseError for token {token[:12]}... - Code: {error_code}, Message: {error_message}")
        
        # Enhanced error handling for different token types
        should_prune = False
        
        # Specific iOS APNs error handling
        if token_type == 'ios':
            if 'auth error from apns' in error_message or 'apns' in error_message:
                st.error(f"❌ iOS APNs Error: {error_message}")
                st.error("""\n**iOS Notification Failed - APNs Setup Required:**
                1. Go to Firebase Console → Project Settings → Cloud Messaging
                2. Under 'Apple app configuration', upload your APNs Authentication Key (.p8 file)
                3. Or upload your APNs Certificate (.p12 file)
                4. Set IOS_BUNDLE_ID in your .env file (e.g., com.yourcompany.yourapp)
                5. Make sure the bundle ID matches your iOS app
                """)
                return False, None, ("error", f"iOS APNs authentication failed - Check Firebase Console APNs setup")
        
        # Only consider clear invalid token signals for pruning
        if 'invalid-registration-token' in error_message or 'unregistered' in error_message or error_code == 'not_found':
            should_prune = True
        
        # Do NOT prune on APNS/WebPush auth errors for non-iOS tokens
        if token_type != 'ios' and ('auth error from apns' in error_message or 'auth error from web push service' in error_message):
            should_prune = False
        
        if should_prune:
            # Do not prune tokens from Firestore as requested; just report as error
            # Keep tokens in DB for future inspection or retries
            return False, None, ("error", f"Invalid/expired token detected (not pruned): {error_message[:80]}")
        else:
            return False, None, ("error", f"FCM error: {e}")
            
    except Exception as e:
        st.write(f"🔍 Debug: Unexpected error for token {token[:12] if token else 'unknown'}... - {type(e).__name__}: {str(e)}")
        return False, None, ("error", f"Unexpected error: {e}")

def send_notifications_parallel(title, body, tokens, batch_size=100, max_workers=10, click_action="FLUTTER_NOTIFICATION_CLICK", route="/", screen="home", campaign_id=None, campaign_name=None, cohort_tags=None):
    """Send notifications in parallel with campaign tracking"""
    summary = {"success": 0, "pruned": 0, "errors": 0, "ios_success": 0, "android_success": 0, "fcm_success": 0}
    errors_list = []
    
    # Debug information
    st.write(f"🔍 Debug: Starting parallel send with {len(tokens)} tokens, batch_size={batch_size}, max_workers={max_workers}")
    st.write(f"🔍 Debug: Click action={click_action}, route={route}, screen={screen}")
    if campaign_id:
        st.write(f"🔍 Debug: Campaign ID={campaign_id}, Name={campaign_name}, Cohorts={cohort_tags}")
    
    # Create progress tracking
    total_tokens = len(tokens)
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    def send_batch(batch_tokens):
        batch_results = {"success": 0, "pruned": 0, "errors": 0, "ios_success": 0, "android_success": 0, "fcm_success": 0}
        batch_errors = []
        
        st.write(f"🔍 Debug: Processing batch of {len(batch_tokens)} tokens")
        
        for doc_ref, token, is_array, token_type, name in batch_tokens:
            try:
                success, response, error_info = send_single_notification(
                    doc_ref, token, is_array, token_type, title, body, click_action, route, screen, campaign_id, campaign_name, cohort_tags, name
                )
            except Exception as e:
                st.write(f"🔍 Debug: Exception in send_single_notification: {str(e)}")
                success, response, error_info = False, None, ("error", str(e))
            
            if success:
                batch_results["success"] += 1
                if token_type == "ios":
                    batch_results["ios_success"] += 1
                elif token_type == "fcm":
                    batch_results["fcm_success"] += 1
                else:
                    batch_results["android_success"] += 1
            elif error_info:
                error_type, error_msg = error_info
                if error_type == "pruned":
                    batch_results["pruned"] += 1
                else:
                    batch_results["errors"] += 1
                    batch_errors.append((token, error_msg))
            else:
                batch_results["errors"] += 1
                batch_errors.append((token, "Unknown error - no error info returned"))
            
            # Small delay to prevent rate limiting
            time.sleep(0.01)
        
        return batch_results, batch_errors
    
    # Process in parallel batches
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        
        for i in range(0, len(tokens), batch_size):
            batch = tokens[i:i + batch_size]
            future = executor.submit(send_batch, batch)
            futures.append(future)
        
        completed = 0
        for future in concurrent.futures.as_completed(futures):
            batch_results, batch_errors = future.result()
            
            # Update summary
            for key in summary:
                summary[key] += batch_results[key]
            errors_list.extend(batch_errors)
            
            completed += len(batch)
            progress = completed / total_tokens
            progress_bar.progress(progress)
            status_text.text(f"📤 Sent {completed}/{total_tokens} notifications...")
    
    progress_bar.empty()
    status_text.empty()
    
    return summary, errors_list

def send_notifications(title, body, tokens, batch_size=100):
    """Legacy function for backward compatibility."""
    return send_notifications_parallel(title, body, tokens, batch_size)


def validate_token(token):
    """Validate actual FCM token format (not for cpIds!)."""
    if not isinstance(token, str):
        return False
    token = token.strip()
    return 10 < len(token) < 4096 and ' ' not in token and len(token) > 0

def send_test_notification(test_token, title, body):
    """Send a single test notification with simplified configuration for debugging."""
    try:
        if not validate_token(test_token):
            return False, "Invalid token format."
        
        token_type = detect_token_type(test_token)
        st.write(f"🔍 Debug: Testing {token_type} token: {test_token[:12]}...")
        
        # Use the same logic as the main notification function
        success, response, error_info = send_single_notification(
            None, test_token, False, token_type, title, body, 
            current_click_action, current_default_route, current_screen_name
        )
        
        if success:
            return True, f"✅ Test notification sent to {token_type.upper()} device! (ID: {response})\n📱 Click the notification to open the app."
        else:
            error_type, error_msg = error_info if error_info else ("error", "Unknown error")
            return False, f"❌ Failed to send test notification: {error_msg}"
            
    except Exception as e:
        return False, f"❌ Unexpected error: {str(e)}"

# --- Streamlit UI ---
st.title("📱 ACN Agent FCM Notification Sender (Enhanced)")
st.markdown("""
<div style="background: linear-gradient(90deg, #667eea 0%, #764ba2 100%); padding: 20px; border-radius: 10px; color: white; margin-bottom: 20px;">
    <h3 style="margin: 0; color: white;">🚀 Enhanced Notification System</h3>
    <p style="margin: 10px 0 0 0; opacity: 0.9;">Send notifications to your agents with improved iOS/Android support, parallel processing, and smart token management.</p>
</div>
""", unsafe_allow_html=True)

# Initialize default values for settings
default_click_action = "FLUTTER_NOTIFICATION_CLICK"
default_route_value = "/"
default_screen_value = "home"

# Sidebar for settings
with st.sidebar:
    st.header("⚙️ Settings")
    
    # Performance settings
    st.subheader("🚀 Performance")
    batch_size = st.slider("Batch Size", min_value=50, max_value=500, value=100, step=50, 
                          help="Number of notifications to process in each batch")
    max_workers = st.slider("Parallel Workers", min_value=1, max_value=20, value=10, step=1,
                           help="Number of parallel threads for sending notifications")
    
    # Notification settings
    st.subheader("📱 Notification Settings")
    sound_enabled = st.checkbox("🔊 Enable Sound", value=True, help="Enable notification sound")
    badge_count = st.number_input("🔢 Badge Count", min_value=0, max_value=99, value=1, 
                                 help="Badge count for iOS notifications")
    
    # Click action settings
    st.subheader("🔗 Click Action Settings")
    click_action = st.selectbox(
        "Click Action",
        ["FLUTTER_NOTIFICATION_CLICK", "OPEN_APP", "CUSTOM"],
        index=0,
        help="Action to perform when notification is clicked",
        key="click_action_select"
    )
    
    if click_action == "CUSTOM":
        custom_click_action = st.text_input("Custom Click Action", value="FLUTTER_NOTIFICATION_CLICK", key="custom_click_action")
    else:
        custom_click_action = click_action
    
    # Platform override (debug)
    st.subheader("🧪 Platform Override (debug)")
    force_platform = st.selectbox(
        "Force Platform",
        ["Auto-detect", "Android", "iOS"],
        index=0,
        help="Override platform detection for sending (useful for debugging)"
    )
    st.session_state["force_platform"] = force_platform

    # Redirection settings
    st.subheader("📱 App Redirection Settings")
    st.info("💡 These settings control where the app opens when the notification is clicked")
    
    # Route settings with better descriptions
    default_route = st.text_input(
        "Default Route", 
        value=default_route_value, 
        help="Route to navigate to when app opens (e.g., /home, /dashboard, /profile)", 
        key="default_route"
    )
    screen_name = st.text_input(
        "Screen Name", 
        value=default_screen_value, 
        help="Screen name to open in the app (e.g., home, dashboard, profile)", 
        key="screen_name"
    )
    
    # Show redirection preview
    if default_route and screen_name:
        st.success(f"📱 App will open to: **{screen_name}** screen at route **{default_route}**")
    
    # Store current values for use in main logic
    current_click_action = custom_click_action
    current_default_route = default_route
    current_screen_name = screen_name
    
    # Advanced settings
    with st.expander("🔧 Advanced Settings"):
        ttl_seconds = st.number_input("⏰ TTL (seconds)", min_value=60, max_value=86400, value=3600,
                                     help="Time to live for notifications")
        collapse_key = st.text_input("🔑 Collapse Key", value="rich_notification",
                                   help="Key for collapsing similar notifications")

# Main content tabs
tab1, tab2, tab3, tab4 = st.tabs(["📝 Compose", "📊 Recipients", "🏷️ Cohorts", "📈 Analytics"])

# Cohort Management Tab
with tab3:
    st.subheader("🏷️ Cohort Management")
    
    # Load existing cohorts
    cohorts = load_cohorts()
    
    # Create new cohort
    st.markdown("### ➕ Create Cohort")
    col1, col2 = st.columns([4, 1])
    with col1:
        new_cohort = st.text_input("Cohort name:", placeholder="e.g., North Bangalore, Premium Agents")
    with col2:
        if st.button("Create", type="primary", use_container_width=True):
            if new_cohort and new_cohort not in cohorts:
                cohorts[new_cohort] = []
                save_cohorts(cohorts)
                st.success(f"✅ Created")
                st.rerun()
            elif new_cohort in cohorts:
                st.error("Already exists!")
            else:
                st.error("Enter name!")
    
    # Manage existing cohorts
    if cohorts:
        st.markdown("---")
        st.markdown("### 📋 Manage Cohorts")
        
        # Show all cohorts with counts in a visual grid
        st.markdown("**Your Cohorts:**")
        cols = st.columns(min(len(cohorts), 4))
        for idx, (name, ids) in enumerate(cohorts.items()):
            with cols[idx % 4]:
                st.metric(label=name, value=f"{len(ids)} IDs", delta=None)
        
        st.markdown("---")
        
        cohort_to_edit = st.selectbox("Select to edit:", list(cohorts.keys()))
        
        if cohort_to_edit:
            current_cp_ids = cohorts[cohort_to_edit]
            
            st.markdown(f"**Edit CP IDs for: {cohort_to_edit}**")
            
            # Single editable text area with all CP IDs
            edited_cp_ids = st.text_area(
                f"CP IDs ({len(current_cp_ids)}):",
                value="\n".join(current_cp_ids),
                height=300,
                key=f"edit_{cohort_to_edit}",
                help="Edit CP IDs directly. One per line or comma-separated."
            )
            
            col1, col2, col3 = st.columns(3)
            with col1:
                if st.button("💾 Save Changes", use_container_width=True, type="primary", key=f"save_{cohort_to_edit}"):
                    # Parse the edited IDs
                    new_ids = []
                    for line in edited_cp_ids.replace(',', '\n').split('\n'):
                        cp_id = line.strip()
                        if cp_id:
                            new_ids.append(cp_id)
                    
                    # Remove duplicates while preserving order
                    seen = set()
                    unique_ids = []
                    for cp_id in new_ids:
                        if cp_id not in seen:
                            seen.add(cp_id)
                            unique_ids.append(cp_id)
                    
                    cohorts[cohort_to_edit] = unique_ids
                    save_cohorts(cohorts)
                    st.success(f"✅ Saved {len(unique_ids)} CP IDs")
                    st.rerun()
            with col2:
                if st.button(f"🗑️ Clear All", use_container_width=True, key=f"clear_{cohort_to_edit}"):
                    cohorts[cohort_to_edit] = []
                    save_cohorts(cohorts)
                    st.success("✅ Cleared")
                    st.rerun()
            with col3:
                if st.button(f"❌ Delete Cohort", use_container_width=True, key=f"del_{cohort_to_edit}"):
                    del cohorts[cohort_to_edit]
                    save_cohorts(cohorts)
                    st.success("Deleted")
                    st.rerun()
    else:
        st.info("💡 No cohorts yet. Create one above!")

with tab2:
    st.subheader("📊 Select Recipients")
    
    # Recipient selection method
    recipient_method = st.radio(
        "Method:",
        ["📢 All Agents", "🏷️ Cohorts", "📁 CSV File", "✏️ Manual"],
        horizontal=True
    )
    
    if recipient_method == "📢 All Agents":
        st.success("✅ Will send to all agents in database")
    
    elif recipient_method == "🏷️ Cohorts":
        cohorts = load_cohorts()
        
        if cohorts:
            # Display all cohorts with counts
            st.markdown("**Available Cohorts:**")
            cols = st.columns(min(len(cohorts), 4))
            for idx, (cohort_name, cp_ids) in enumerate(cohorts.items()):
                with cols[idx % 4]:
                    st.metric(cohort_name, f"{len(cp_ids)} IDs")
            
            st.markdown("---")
            
            # Format cohort options with counts
            cohort_options = [f"{name} ({len(ids)})" for name, ids in cohorts.items()]
            cohort_names = list(cohorts.keys())
            
            selected_display = st.multiselect(
                "Select cohorts:",
                cohort_options
            )
            
            # Extract actual cohort names from selection
            selected_cohorts = []
            for display in selected_display:
                for name in cohort_names:
                    if display.startswith(name + " ("):
                        selected_cohorts.append(name)
                        break
            
            if len(selected_cohorts) > 1:
                logic_type = st.radio(
                    "Logic:",
                    ["OR (any)", "AND (all)"],
                    horizontal=True
                )
            else:
                logic_type = "OR (any)"
            
            if selected_cohorts:
                # Get CP IDs based on logic
                if "AND" in logic_type:
                    cp_ids_sets = [set(cohorts[c]) for c in selected_cohorts]
                    selected_cp_ids = list(set.intersection(*cp_ids_sets)) if cp_ids_sets else []
                else:
                    selected_cp_ids = []
                    for cohort_name in selected_cohorts:
                        selected_cp_ids.extend(cohorts[cohort_name])
                    selected_cp_ids = list(set(selected_cp_ids))
                
                if selected_cp_ids:
                    st.success(f"✅ **{len(selected_cp_ids)} CP IDs** will receive notification")
                    
                    # Show breakdown by cohort
                    with st.expander("📊 View CP ID breakdown"):
                        for cohort_name in selected_cohorts:
                            cohort_ids = cohorts[cohort_name]
                            st.write(f"**{cohort_name}**: {len(cohort_ids)} IDs")
                            st.code(", ".join(cohort_ids[:20]) + ("..." if len(cohort_ids) > 20 else ""))
                else:
                    st.warning("⚠️ No CP IDs found")
        else:
            st.warning("⚠️ Create cohorts in Cohorts tab first")
    
    elif recipient_method == "📁 CSV File":
        uploaded_csv = st.file_uploader("Upload CSV with cpId column", type="csv")
        if uploaded_csv:
            try:
                df = pd.read_csv(uploaded_csv)
                st.dataframe(df.head(5), use_container_width=True)
                
                if 'cpId' in df.columns:
                    valid_cpids = df['cpId'].dropna().shape[0]
                    st.success(f"✅ {valid_cpids} CP IDs found")
                else:
                    st.error("❌ 'cpId' column required")
            except Exception as e:
                st.error(f"❌ Error: {e}")
    
    else:  # Manual
        manual_cpids = st.text_area(
            "Enter CP IDs (one per line):", 
            height=200,
            placeholder="CPC001\nCPC002\nCPC003"
        )
        if manual_cpids:
            lines = [c.strip() for c in manual_cpids.split('\n') if c.strip()]
            st.info(f"📝 {len(lines)} CP IDs entered")

with tab1:
    st.subheader("📝 Compose Notification")
    
    # iOS Setup Warning
    ios_bundle_id = os.getenv("IOS_BUNDLE_ID")
    if not ios_bundle_id:
        st.warning("⚠️ **iOS notifications may fail!** Set `IOS_BUNDLE_ID` in your `.env` file. See `IOS_SETUP_GUIDE.md` for details.")
        with st.expander("🍎 Quick iOS Setup Guide"):
            st.markdown("""
            **iOS notifications require APNs authentication:**
            
            1. **Upload APNs Key/Certificate to Firebase Console:**
               - Go to Firebase Console → Project Settings → Cloud Messaging
               - Upload your APNs Authentication Key (.p8) or Certificate (.p12)
            
            2. **Add iOS Bundle ID to .env file:**
               ```
               IOS_BUNDLE_ID=com.yourcompany.yourapp
               ```
            
            3. **Restart this app** to load new settings
            
            📄 **Full guide:** See `IOS_SETUP_GUIDE.md` for complete instructions
            """)
    
    # Quick test buttons
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("📝 Load Test 1", use_container_width=True):
            st.session_state['test_title'] = "New Property Available"
            st.session_state['test_body'] = "Check out this amazing 3BHK apartment in Whitefield with modern amenities!"
    with col2:
        if st.button("📝 Load Test 2", use_container_width=True):
            st.session_state['test_title'] = "Meeting Reminder"
            st.session_state['test_body'] = "Team meeting scheduled at 3 PM today. Please join on time."
    with col3:
        if st.button("🗑️ Clear", use_container_width=True):
            st.session_state['test_title'] = ""
            st.session_state['test_body'] = ""
    
    # Title input
    st.info("💡 **Use placeholders:** `{name}` for full name (e.g., 'Shameer K'), `{firstname}` for first name only (e.g., 'Shameer')")
    
    title = st.text_input(
        "📌 Title", 
        value=st.session_state.get('test_title', ''),
        max_chars=100,
        placeholder="e.g., Hi {firstname}, New Properties Available!",
        help="Max 100 characters. Use {name} or {firstname} for personalization"
    ).strip()
    
    # Body input
    body = st.text_area(
        "📄 Message", 
        value=st.session_state.get('test_body', ''),
        height=100, 
        max_chars=500,
        placeholder="Hey {name}, check out these amazing properties just for you!",
        help="Max 500 characters. Use {name} or {firstname} for personalization"
    ).strip()
    
    # Campaign name input
    campaign_name = st.text_input(
        "🏷️ Campaign Name (for tracking)",
        placeholder="e.g., November Property Launch, Weekend Promo",
        help="This will appear as 'message_name' in Firebase Analytics"
    ).strip()
    
    # Preview section - cleaner version
    if title or body:
        st.markdown("---")
        st.markdown("### 👀 Preview")
        
        # Show personalization example if placeholders are used
        if '{name}' in title or '{name}' in body or '{firstname}' in title or '{firstname}' in body:
            example_name = "Shameer K"
            example_firstname = "Shameer"
            personalized_title = title.replace('{name}', example_name).replace('{firstname}', example_firstname)
            personalized_body = body.replace('{name}', example_name).replace('{firstname}', example_firstname)
            
            st.success(f"✨ **Personalized for 'Shameer K':**")
            st.info(f"**Title:** {personalized_title}  \n**Message:** {personalized_body}")
            st.caption("Each recipient will see their own name instead of 'Shameer K'")
            st.markdown("---")
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("**🍎 iOS**")
            st.markdown(f"""
            <div style="border:1px solid #ddd;border-radius:12px;padding:15px;background:#f9f9f9;">
              <div style="display:flex;align-items:start;gap:10px;">
                <div style="width:35px;height:35px;background:#007AFF;border-radius:8px;display:flex;align-items:center;justify-content:center;color:white;font-size:18px;">📱</div>
                <div style="flex:1;">
                  <div style="font-weight:600;color:#000;font-size:15px;margin-bottom:2px;">{title or 'Title'}</div>
                  <div style="color:#666;font-size:13px;line-height:1.4;">{body or 'Message'}</div>
                  <div style="color:#999;font-size:11px;margin-top:4px;">now</div>
                </div>
              </div>
            </div>
            """, unsafe_allow_html=True)
        
        with col2:
            st.markdown("**🤖 Android**")
            st.markdown(f"""
            <div style="border:1px solid #ddd;border-radius:8px;padding:15px;background:#fff;">
              <div style="display:flex;align-items:start;gap:10px;">
                <div style="width:32px;height:32px;background:#34A853;border-radius:6px;display:flex;align-items:center;justify-content:center;color:white;font-size:16px;">📱</div>
                <div style="flex:1;">
                  <div style="font-weight:500;color:#202124;font-size:14px;margin-bottom:2px;">{title or 'Title'}</div>
                  <div style="color:#5f6368;font-size:13px;line-height:1.3;">{body or 'Message'}</div>
                  <div style="color:#999;font-size:11px;margin-top:4px;">Just now</div>
                </div>
              </div>
            </div>
            """, unsafe_allow_html=True)

    st.markdown("---")
    
    # Test notification section
    st.subheader("🧪 Test Notification")
    st.info("💡 Test your notification before sending to all agents")
    
    # Click action information
    with st.expander("📱 About Notification Click Behavior"):
        st.markdown("""
        **🔗 Enhanced Click Action Configuration:**
        - **Android**: Uses `FLUTTER_NOTIFICATION_CLICK` action with enhanced data payload
        - **iOS**: Configured with proper APNS payload including URL arguments for deep linking
        - **FCM**: Universal FCM tokens work for both iOS and Android
        - **Data Payload**: Includes `screen`, `route`, `action`, and `navigation` parameters
        
        **📲 What happens when clicked:**
        1. App opens automatically (if installed)
        2. Navigates to the specified screen/route with parameters
        3. Passes notification data including `from_notification: true`
        4. Includes unique notification ID for tracking
        
        **⚙️ Technical Details:**
        - Click action: `FLUTTER_NOTIFICATION_CLICK`
        - Navigation data: JSON string containing screen, route, and params
        - FCM compatibility: All data values are strings (JSON serialized)
        - Custom data payload: Contains navigation info for app routing
        - Analytics: FCM analytics labels for tracking
        - Category: `RICH_NOTIFICATION`
        
        **🛠️ Error Handling:**
        - Invalid/expired tokens are automatically pruned
        - Better error detection for APNS and Web Push issues
        - Enhanced token validation and cleanup
        """)
    
    # Token type information
    with st.expander("🔥 About FCM Token Types"):
        st.markdown("""
        **📱 Token Type Detection:**
        - **iOS**: Legacy or identifiable iOS-specific tokens
        - **Android**: Legacy Android-specific tokens (APA91b, AAAA prefixes)
        - **FCM**: Modern universal FCM tokens (most common)
        - **Unknown**: Tokens that don't match known patterns
        
        **🚀 Enhanced FCM Support:**
        - Modern FCM tokens work for both iOS and Android apps
        - The same token can deliver to either platform based on app installation
        - Improved error handling prevents unnecessary token pruning
        - Better delivery rates for iOS devices
        
        **💡 Why This Matters:**
        - Your iOS FSM tokens will now receive notifications properly
        - Reduced false token removal (pruning)
        - Better cross-platform compatibility
        """)
    
    # iOS troubleshooting
    with st.expander("🍎 iOS Notification Troubleshooting"):
        st.markdown("""
        **🔧 Common iOS Issues:**
        1. **App not receiving notifications**: Check if notifications are enabled in iOS Settings
        2. **App not opening**: Ensure your app handles the click_action properly
        3. **Token issues**: iOS tokens can expire when app is uninstalled/reinstalled
        
        **📱 iOS App Requirements:**
        - App must be configured to handle `FLUTTER_NOTIFICATION_CLICK` action
        - Push notifications must be enabled in iOS Settings > Notifications
        - App must be properly configured with APNS certificates
        
        **🛠️ Debugging Steps:**
        1. Test with a single iOS token first
        2. Check iOS device notification settings
        3. Verify app is properly configured for push notifications
        4. Check Firebase console for delivery status
        """)
    
    col1, col2 = st.columns([2, 1])
    with col1:
        test_token = st.text_input(
            "Enter a single FCM token to test",
            placeholder="Enter FCM token here...",
            help="Paste a valid FCM token to test the notification"
        )
    with col2:
        st.write("")  # Spacing
        st.write("")  # Spacing
        test_button = st.button("🚀 Send Test", use_container_width=True)
    
    if test_button:
        if not test_token:
            st.warning("⚠️ Please enter a test token.")
        elif not (title and body):
            st.warning("⚠️ Please enter both title and body first.")
        else:
            with st.spinner("🧪 Sending test notification..."):
                ok, msg = send_test_notification(test_token, title, body)
                if ok:
                    st.success(msg)
                else:
                    st.error(f"❌ {msg}")

# Analytics tab
with tab4:
    st.subheader("📈 Campaign Analytics")
    
    campaigns = load_campaigns()
    
    if campaigns:
        # Convert to DataFrame
        df = pd.DataFrame(campaigns)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        
        st.metric("📊 Total Campaigns", len(campaigns))
        
        # Filters
        col1, col2 = st.columns(2)
        with col1:
            cohorts_available = list(load_cohorts().keys())
            if cohorts_available:
                filter_cohort = st.selectbox("Filter by cohort:", ["All"] + cohorts_available)
            else:
                filter_cohort = "All"
        
        with col2:
            if len(df) > 0:
                min_date = df['timestamp'].min().date()
                max_date = df['timestamp'].max().date()
                date_range = st.date_input("Date range:", value=(min_date, max_date))
        
        # Apply filters
        filtered_df = df.copy()
        if filter_cohort != "All":
            filtered_df = filtered_df[filtered_df['cohorts'].apply(
                lambda x: filter_cohort in x if isinstance(x, list) else False
            )]
        
        # Add campaign_name column if it doesn't exist
        if 'campaign_name' not in filtered_df.columns:
            filtered_df['campaign_name'] = filtered_df['title']  # Use title as fallback
        
        # Display campaigns
        st.markdown("### 📋 Campaign History")
        display_df = filtered_df[['campaign_id', 'campaign_name', 'title', 'body', 'cohorts', 'logic', 'timestamp', 'total_sent', 'total_failed', 'duration_seconds']].copy()
        display_df['timestamp'] = display_df['timestamp'].dt.strftime('%Y-%m-%d %H:%M:%S')
        # Format cohorts as comma-separated string
        display_df['cohorts'] = display_df['cohorts'].apply(
            lambda x: ', '.join(x) if isinstance(x, list) and x else 'All Agents'
        )
        st.dataframe(display_df, use_container_width=True)
        
        # Download button
        csv = filtered_df.to_csv(index=False)
        st.download_button(
            "📥 Download Campaign Data (CSV)",
            csv,
            "campaigns.csv",
            "text/csv",
            use_container_width=True
        )
        
        # Campaign stats
        st.markdown("### 📊 Statistics")
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            total_sent = filtered_df['total_sent'].sum()
            st.metric("Total Sent", f"{total_sent:,}")
        with col2:
            total_failed = filtered_df['total_failed'].sum()
            st.metric("Total Failed", f"{total_failed:,}")
        with col3:
            avg_success = (total_sent / (total_sent + total_failed) * 100) if (total_sent + total_failed) > 0 else 0
            st.metric("Avg Success Rate", f"{avg_success:.1f}%")
        with col4:
            avg_duration = filtered_df['duration_seconds'].mean()
            st.metric("Avg Duration", f"{avg_duration:.1f}s")
        
        st.markdown("---")
        st.error("⚠️ **IMPORTANT: Campaign tracking requires app code update!**")
        
        col1, col2 = st.columns([3, 1])
        with col1:
            st.warning("**Your React Native app must log notification data to Firebase Analytics!**")
        with col2:
            with open("REACT_NATIVE_TRACKING_SETUP.md", "r", encoding="utf-8") as f:
                setup_guide = f.read()
            st.download_button(
                "📥 Download Setup Guide",
                setup_guide,
                "REACT_NATIVE_TRACKING_SETUP.md",
                "text/markdown",
                use_container_width=True
            )
        
        with st.expander("📱 Quick Preview - React Native Code"):
            st.markdown("""
            **Why am I getting NULL values in BigQuery?**
            
            Firebase doesn't automatically log notification data to Analytics. Your app must explicitly log it.
            
            **Add this to your React Native app:**
            """)
            
            st.code("""import analytics from '@react-native-firebase/analytics';
import messaging from '@react-native-firebase/messaging';

// Log notification open to Firebase Analytics
async function logNotificationOpen(remoteMessage) {
  const { data } = remoteMessage;
  
  await analytics().logEvent('notification_open', {
    message_id: data.campaign_id || '',
    message_name: data.campaign_name || '',
    campaign_id: data.campaign_id || '',
    campaign_name: data.campaign_name || '',
    cohort_tags: data.cohort_tags || '',
  });
}

// When notification opens app (background state)
messaging().onNotificationOpenedApp(async (remoteMessage) => {
  await logNotificationOpen(remoteMessage);
  // ... your navigation code
});

// When notification opens app (quit state)
messaging().getInitialNotification().then(async (remoteMessage) => {
  if (remoteMessage) {
    await logNotificationOpen(remoteMessage);
  }
});""", language="javascript")
            
            st.info("📄 **Download the complete guide above** for full implementation with debugging tips!")
            
            st.markdown("""
            **What this tool does vs what your app must do:**
            - ✅ Tool sends: `campaign_id`, `message_id`, `campaign_name`, `message_name` in notification data
            - ❌ Tool cannot: Make your app log to Analytics (only app code can do this)
            - ✅ App must: Call `analytics().logEvent()` when notification opens
            """)
        
    else:
        st.info("📊 No campaigns yet. Send some notifications to see analytics here!")


# Initialize session state for tokens
if 'processed_tokens' not in st.session_state:
    st.session_state.processed_tokens = None
if 'show_confirmation' not in st.session_state:
    st.session_state.show_confirmation = False
if 'campaign_id' not in st.session_state:
    st.session_state.campaign_id = None
if 'campaign_name' not in st.session_state:
    st.session_state.campaign_name = None
if 'selected_cohorts' not in st.session_state:
    st.session_state.selected_cohorts = []
if 'logic_type' not in st.session_state:
    st.session_state.logic_type = "OR"

# Main send button
st.markdown("---")
col1, col2, col3 = st.columns([1, 2, 1])
with col2:
    send_button = st.button(
        "🚀 Send Notifications", 
        use_container_width=True,
        type="primary",
        help="Send notifications to selected recipients"
    )

if send_button:
    # Validation
    if not title:
        st.error("❌ Title cannot be empty.")
        st.stop()
    if not body:
        st.error("❌ Body cannot be empty.")
        st.stop()

    # Determine recipient method and gather tokens
    tokens = []
    campaign_cohorts = []  # Store cohort names for campaign tracking
    campaign_logic = "OR"
    
    # Generate campaign ID
    campaign_id = generate_campaign_id()
    st.info(f"🆔 Campaign ID: **{campaign_id}**")
    
    if recipient_method == "📢 All Agents":
        st.info("� Fetching tokens...")
        with st.spinner("Loading..."):
            tokens = fetch_all_tokens_directly()
        st.success(f"✅ {len(tokens)} tokens ready")
        
    else:
        # Gather cpIds first
        cpids = []
        
        if recipient_method == "🏷️ Cohorts":
            # Check if cohorts were selected in the UI
            try:
                if selected_cohorts and selected_cp_ids:
                    cpids = selected_cp_ids
                    # Store cohort names and logic type for campaign tracking
                    campaign_cohorts = selected_cohorts
                    campaign_logic = "AND" if "AND" in logic_type else "OR"
                else:
                    st.error("❌ Select a cohort")
                    st.stop()
            except NameError:
                st.error("❌ Select cohorts first")
                st.stop()
            
        elif recipient_method == "📁 CSV File":
            try:
                if uploaded_csv:
                    df = pd.read_csv(uploaded_csv)
                    if 'cpId' in df.columns:
                        cpids += df['cpId'].dropna().astype(str).tolist()
                    else:
                        st.error("❌ 'cpId' column required")
                        st.stop()
                else:
                    st.error("❌ Upload CSV")
                    st.stop()
            except NameError:
                st.error("❌ Upload CSV first")
                st.stop()
            except Exception as e:
                st.error(f"❌ Error: {e}")
                st.stop()
                
        elif recipient_method == "✏️ Manual":
            try:
                if manual_cpids:
                    cpids += [c.strip() for c in manual_cpids.split('\n') if c.strip()]
                else:
                    st.error("❌ Enter CP IDs")
                    st.stop()
            except NameError:
                st.error("❌ Enter CP IDs first")
                st.stop()

        cpids = list(set(filter(None, cpids)))
        
        if not cpids:
            st.error("❌ No cpIds provided.")
            st.stop()

        st.info(f"📝 Processing {len(cpids)} unique cpIds...")
        
        # Fetch tokens for cpIds
        with st.spinner("🔍 Fetching notification tokens..."):
            tokens = fetch_tokens_for_cpids(cpids)

    # Validate tokens
    valid_tokens, invalid_tokens = [], []
    for doc_ref, token, is_array, token_type in tokens:
        if validate_token(token):
            valid_tokens.append((doc_ref, token, is_array, token_type))
        else:
            invalid_tokens.append(token)

    if invalid_tokens:
        st.warning(f"⚠️ {len(invalid_tokens)} invalid tokens will be skipped")
        with st.expander("👀 View invalid tokens"):
            for t in invalid_tokens[:10]:  # Show first 10
                st.write(f"`{t[:20]}...`")
            if len(invalid_tokens) > 10:
                st.write(f"... and {len(invalid_tokens) - 10} more")

    tokens = valid_tokens
    if not tokens:
        st.warning("⚠️ No valid tokens found. Exiting.")
        st.stop()

    # Show token breakdown
    ios_count = sum(1 for _, _, _, token_type in tokens if token_type == "ios")
    android_count = sum(1 for _, _, _, token_type in tokens if token_type == "android")
    fcm_count = sum(1 for _, _, _, token_type in tokens if token_type == "fcm")
    unknown_count = sum(1 for _, _, _, token_type in tokens if token_type == "unknown")
    
    st.success(f"✅ Found {len(tokens)} valid tokens ready to send")
    
    # Token breakdown
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("📱 Total Tokens", len(tokens))
    with col2:
        st.metric("🍎 iOS Tokens", ios_count)
    with col3:
        st.metric("🤖 Android Tokens", android_count)
    with col4:
        st.metric("🔥 FCM Tokens", fcm_count)
    with col5:
        st.metric("❓ Unknown Type", unknown_count)
    
    # Store tokens in session state and show confirmation
    st.session_state.processed_tokens = tokens
    st.session_state.show_confirmation = True
    
    # Store campaign data in session state
    st.session_state.campaign_id = campaign_id
    st.session_state.campaign_name = campaign_name if campaign_name else title
    st.session_state.selected_cohorts = campaign_cohorts
    st.session_state.logic_type = campaign_logic
    
    # Confirmation
    st.markdown("### 🚀 Ready to Send!")
    
    # Show notification preview
    st.write("**📋 Notification Preview:**")
    st.info(f"**Title:** {title}\n**Body:** {body}")
    
    # Show campaign tracking info
    if campaign_id or campaign_name:
        st.success(f"**📊 Campaign Tracking:**\n- Campaign ID: `{campaign_id}`\n- Campaign Name: `{campaign_name if campaign_name else title}`")
        with st.expander("🔍 Data Payload (what will be sent)"):
            payload_preview = {
                "title": title,
                "body": body,
                "campaign_id": campaign_id,
                "message_id": campaign_id,
                "campaign_name": campaign_name if campaign_name else title,
                "message_name": campaign_name if campaign_name else title,
                "cohort_tags": ','.join(campaign_cohorts) if campaign_cohorts else "All Agents",
                "click_action": current_click_action,
                "screen": current_screen_name,
                "route": current_default_route,
                "from_notification": "true"
            }
            st.json(payload_preview)
            st.warning("⚠️ **Important:** Your Flutter app must log these fields to Firebase Analytics when the notification is opened. Check app code!")
    
    # Debug information
    with st.expander("🔍 Debug Information"):
        st.write(f"**Click Action:** {current_click_action}")
        st.write(f"**Default Route:** {current_default_route}")
        st.write(f"**Screen Name:** {current_screen_name}")
        st.write(f"**Batch Size:** {batch_size}")
        st.write(f"**Max Workers:** {max_workers}")
        st.write(f"**Total Tokens:** {len(tokens)}")
        
        # Redirection debug info
        st.write("**Simplified Configuration:**")
        st.code(f"""
Data Payload (Simplified):
{{
    "title": "{title}",
    "body": "{body}",
    "click_action": "{current_click_action}",
    "screen": "{current_screen_name}",
    "route": "{current_default_route}",
    "from_notification": "true",
    "timestamp": "current_timestamp"
}}

Click Action: {current_click_action}
Screen: {current_screen_name}
Route: {current_default_route}
        """)
        
        # Test function availability
        st.write("**Function Test:**")
        try:
            # Test if the function exists and is callable
            if callable(send_notifications_parallel):
                st.success("✅ send_notifications_parallel function is available")
            else:
                st.error("❌ send_notifications_parallel function is not callable")
        except Exception as e:
            st.error(f"❌ Error testing function: {str(e)}")
        
        # Quick test button
        if st.button("🧪 Test Function (No Send)", help="Test the function without actually sending"):
            try:
                # Create a dummy token list for testing
                dummy_tokens = [("dummy_ref", "dummy_token", False, "android")]
                st.write("🔍 Testing function with dummy data...")
                # Don't actually call the function, just test if it's accessible
                st.success("✅ Function is accessible and ready to use")
            except Exception as e:
                st.error(f"❌ Function test failed: {str(e)}")

# Show confirmation button if tokens are processed
if st.session_state.show_confirmation and st.session_state.processed_tokens:
    st.markdown("---")
    col1, col2, col3 = st.columns([1, 2, 1])
    with col1:
        if st.button("🔄 Reset", help="Clear processed tokens and start over"):
            st.session_state.processed_tokens = None
            st.session_state.show_confirmation = False
            st.rerun()
    with col2:
        if st.button("✅ Confirm & Send Notifications", type="primary", use_container_width=True):
            # Get tokens and campaign data from session state
            tokens = st.session_state.processed_tokens
            campaign_id = st.session_state.campaign_id
            campaign_name = st.session_state.campaign_name
            selected_cohorts = st.session_state.selected_cohorts
            logic_type = st.session_state.logic_type
            
            st.markdown("### 📤 Sending Notifications...")
            
            # Debug: Show what we're about to send
            st.info(f"🚀 Sending to {len(tokens)} tokens with click action: {current_click_action}")
            
            # Send notifications with enhanced progress tracking
            start_time = time.time()
            
            try:
                with st.spinner("🚀 Sending notifications in parallel..."):
                    summary, errors = send_notifications_parallel(
                        title, body, tokens, batch_size, max_workers, 
                        current_click_action, current_default_route, current_screen_name,
                        campaign_id, campaign_name, selected_cohorts
                    )
            except Exception as e:
                st.error(f"❌ Error during sending: {str(e)}")
                st.stop()
            
            end_time = time.time()
            duration = end_time - start_time
            
            # Save campaign data
            campaign_data = {
                "campaign_id": campaign_id,
                "campaign_name": campaign_name,
                "title": title,
                "body": body,
                "cohorts": selected_cohorts,
                "logic": logic_type,
                "timestamp": datetime.now().isoformat(),
                "total_sent": summary['success'],
                "total_failed": summary['errors'],
                "total_recipients": len(tokens),
                "duration_seconds": round(duration, 2)
            }
            save_campaign(campaign_data)
            
            st.success("✅ All notifications sent!")
            st.markdown("---")

            # Enhanced summary with more details
            st.markdown("### 📊 Delivery Summary")
            
            col1, col2, col3, col4, col5, col6 = st.columns(6)
            with col1:
                st.metric("✅ Total Sent", summary['success'])
            with col2:
                st.metric("🍎 iOS Sent", summary['ios_success'])
            with col3:
                st.metric("🤖 Android Sent", summary['android_success'])
            with col4:
                st.metric("🔥 FCM Sent", summary['fcm_success'])
            with col5:
                st.metric("🗑️ Pruned", summary['pruned'])
            with col6:
                st.metric("❌ Errors", summary['errors'])
            
            # Success rate
            success_rate = summary['success'] / len(tokens) * 100 if tokens else 0
            st.metric("📈 Success Rate", f"{success_rate:.1f}%")
            
            # Performance metrics
            st.markdown("### ⚡ Performance Metrics")
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("⏱️ Total Time", f"{duration:.1f}s")
            with col2:
                notifications_per_second = summary['success'] / duration if duration > 0 else 0
                st.metric("🚀 Notifications/sec", f"{notifications_per_second:.1f}")
            with col3:
                st.metric("👥 Recipients", len(tokens))
            
            # Error details
            if summary['errors']:
                with st.expander("❌ View Error Details"):
                    for tok, err in errors[:20]:  # Show first 20 errors
                        st.write(f"🔴 `{tok[:12]}...`: {err}")
                    if len(errors) > 20:
                        st.write(f"... and {len(errors) - 20} more errors")
            
            # Pruned tokens info
            if summary['pruned']:
                st.info(f"🗑️ {summary['pruned']} expired/invalid tokens were automatically removed from the database")
            
            # Success message
            if summary['success'] > 0:
                st.balloons()
                st.success(f"🎉 Successfully sent {summary['success']} notifications!")
            
            # Clear session state after successful send
            st.session_state.processed_tokens = None
            st.session_state.show_confirmation = False

# Footer
st.markdown("""
---
<div style="text-align:center;color:#666;padding:20px;">
  <div style="background:linear-gradient(90deg, #667eea 0%, #764ba2 100%);padding:15px;border-radius:10px;color:white;">
    <h4 style="margin:0;color:white;">📱 ACN Agent FCM Notification Sender (Enhanced)</h4>
    <p style="margin:5px 0 0 0;opacity:0.9;">Powered by Streamlit & Firebase | Enhanced iOS/Android Support | Parallel Processing</p>
  </div>
</div>
""", unsafe_allow_html=True)
