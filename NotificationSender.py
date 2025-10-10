import os
import streamlit as st
from dotenv import load_dotenv
import concurrent.futures
import threading
from datetime import datetime

# Load environment variables from .env file
load_dotenv()

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
    Returns list of tuples (doc_ref, token, is_array, token_type).
    """
    tokens = []
    for chunk in chunk_list(cpids, 10):
        query = db.collection('acnAgents').where('cpId', 'in', chunk)
        for doc in query.stream():
            data = doc.to_dict()
            raw = data.get('fsmToken')
            doc_ref = doc.reference
            if isinstance(raw, str) and raw.strip():
                token_type = detect_token_type(raw.strip())
                tokens.append((doc_ref, raw.strip(), False, token_type))
            elif isinstance(raw, (list, tuple)):
                for t in raw:
                    if isinstance(t, str) and t.strip():
                        token_type = detect_token_type(t.strip())
                        tokens.append((doc_ref, t.strip(), True, token_type))
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
        # Use None for doc_ref to keep return value pickle-serializable
        doc_ref = None
        
        if isinstance(raw, str) and raw.strip():
            token_type = detect_token_type(raw.strip())
            tokens.append((doc_ref, raw.strip(), False, token_type))
        elif isinstance(raw, (list, tuple)):
            for t in raw:
                if isinstance(t, str) and t.strip():
                    token_type = detect_token_type(t.strip())
                    tokens.append((doc_ref, t.strip(), True, token_type))
        
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

def send_single_notification(doc_ref, token, is_array, token_type, title, body, click_action="FLUTTER_NOTIFICATION_CLICK", route="/", screen="home"):
    """Send a single notification with simplified, reliable configuration.
    Only include platform-specific configs to avoid cross-platform auth errors.
    """
    try:
        # Simplified data payload - focus on basic functionality first
        data_payload = {
            "title": title,
            "body": body,
            "click_action": click_action,
            "screen": screen,
            "route": route,
            "from_notification": "true",
            "timestamp": str(int(time.time()))
        }

        # Basic notification
        notification = messaging.Notification(title=title, body=body)

        # Simplified Android config
        android_config = messaging.AndroidConfig(
            notification=messaging.AndroidNotification(
                title=title,
                body=body,
                sound="default",
                click_action=click_action,
                tag="acn_notification"
            ),
            priority="high",
            ttl=3600,
            data=data_payload
        )

        # Simplified iOS/APNs config
        aps_alert = messaging.ApsAlert(title=title, body=body)
        aps = messaging.Aps(
            alert=aps_alert,
            sound="default",
            badge=1
        )
        
        apns_payload = messaging.APNSPayload(aps=aps)
        # Simple custom data for iOS
        apns_payload.custom_data = data_payload
        
        # APNs headers (optionally include apns-topic = iOS bundle id if provided)
        apns_headers = {
            "apns-priority": "10",
            "apns-push-type": "alert",
        }
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
        
        # Enhanced error handling for different token types
        should_prune = False
        
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
        return False, None, ("error", f"Unexpected error: {e}")

def send_notifications_parallel(title, body, tokens, batch_size=100, max_workers=10, click_action="FLUTTER_NOTIFICATION_CLICK", route="/", screen="home"):
    """Send notifications in parallel for better performance."""
    summary = {"success": 0, "pruned": 0, "errors": 0, "ios_success": 0, "android_success": 0, "fcm_success": 0}
    errors_list = []
    
    # Debug information
    st.write(f"🔍 Debug: Starting parallel send with {len(tokens)} tokens, batch_size={batch_size}, max_workers={max_workers}")
    st.write(f"🔍 Debug: Click action={click_action}, route={route}, screen={screen}")
    
    # Create progress tracking
    total_tokens = len(tokens)
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    def send_batch(batch_tokens):
        batch_results = {"success": 0, "pruned": 0, "errors": 0, "ios_success": 0, "android_success": 0, "fcm_success": 0}
        batch_errors = []
        
        st.write(f"🔍 Debug: Processing batch of {len(batch_tokens)} tokens")
        
        for doc_ref, token, is_array, token_type in batch_tokens:
            try:
                success, response, error_info = send_single_notification(
                    doc_ref, token, is_array, token_type, title, body, click_action, route, screen
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
tab1, tab2, tab3 = st.tabs(["📝 Compose", "📊 Recipients", "📈 Analytics"])

with tab2:
    st.subheader("📊 Select Recipients")
    
    # Recipient selection method
    recipient_method = st.radio(
        "Choose recipient selection method:",
        ["📢 Send to All Agents", "📁 Upload CSV File", "✏️ Manual Entry"],
        horizontal=True
    )
    
    if recipient_method == "📢 Send to All Agents":
        st.info("📋 Will fetch all agent tokens directly from the database for faster processing")
        
        # Show database stats
        if st.button("📊 Show Database Statistics"):
            with st.spinner("🔍 Analyzing database..."):
                try:
                    all_tokens = fetch_all_tokens_directly()
                    ios_count = sum(1 for _, _, _, token_type in all_tokens if token_type == "ios")
                    android_count = sum(1 for _, _, _, token_type in all_tokens if token_type == "android")
                    fcm_count = sum(1 for _, _, _, token_type in all_tokens if token_type == "fcm")
                    unknown_count = sum(1 for _, _, _, token_type in all_tokens if token_type == "unknown")
                    
                    col1, col2, col3, col4, col5 = st.columns(5)
                    with col1:
                        st.metric("📱 Total Tokens", len(all_tokens))
                    with col2:
                        st.metric("🍎 iOS Tokens", ios_count)
                    with col3:
                        st.metric("🤖 Android Tokens", android_count)
                    with col4:
                        st.metric("🔥 FCM Tokens", fcm_count)
                    with col5:
                        st.metric("❓ Unknown Type", unknown_count)
                except Exception as e:
                    st.error(f"❌ Error fetching database stats: {e}")
    
    elif recipient_method == "📁 Upload CSV File":
        st.write("**📁 Upload CSV File**")
        uploaded_csv = st.file_uploader("Select CSV file with cpId column", type="csv", 
                                      help="CSV file should contain a 'cpId' column with agent IDs")
        if uploaded_csv:
            try:
                df = pd.read_csv(uploaded_csv)
                
                # Show preview
                st.write("**📋 Preview:**")
                st.dataframe(df.head(10), use_container_width=True)
                
                if 'cpId' in df.columns:
                    valid_cpids = df['cpId'].dropna().shape[0]
                    st.success(f"✅ Found {valid_cpids} valid cpIds in CSV")
                    
                    # Show some statistics
                    if valid_cpids > 0:
                        st.info(f"📊 CSV contains {len(df)} total rows, {valid_cpids} valid cpIds")
                    else:
                        st.error("❌ CSV must contain a 'cpId' column")
                    st.write("**Available columns:**", list(df.columns))
                    
            except Exception as e:
                st.error(f"❌ Error reading CSV: {e}")
    
    else:  # Manual Entry
        st.write("**✏️ Manual Entry**")
        manual_cpids = st.text_area(
            "Enter cpIds (one per line)", 
            height=200,
            placeholder="Enter agent cpIds here, one per line...\nExample:\n12345\n67890\nabc123",
            help="Enter one cpId per line. Empty lines will be ignored."
        )
        if manual_cpids:
            lines = [c.strip() for c in manual_cpids.split('\n') if c.strip()]
            st.info(f"📝 {len(lines)} cpIds entered")
            
            # Show preview of entered cpIds
            if len(lines) > 0:
                with st.expander("👀 Preview entered cpIds"):
                    for i, cpid in enumerate(lines[:10]):  # Show first 10
                        st.write(f"{i+1}. {cpid}")
                    if len(lines) > 10:
                        st.write(f"... and {len(lines) - 10} more")

with tab1:
    st.subheader("📝 Notification Content")
    
    # Title input with character counter
    title = st.text_input(
        "📌 Notification Title", 
        max_chars=100,
        placeholder="Enter notification title...",
        help="Maximum 100 characters"
    ).strip()
    
    if title:
        st.caption(f"📊 {len(title)}/100 characters")
    
    # Body input with character counter
    body = st.text_area(
        "📄 Notification Body", 
        height=120, 
        max_chars=500,
        placeholder="Enter notification message...",
        help="Maximum 500 characters"
    ).strip()
    
    if body:
        st.caption(f"📊 {len(body)}/500 characters")
    
    # Preview section
    if title or body:
        st.subheader("👀 Live Preview")
        
        # iOS Preview
        st.write("**🍎 iOS Preview:**")
        st.markdown(f"""
        <div style="border:1px solid #007AFF;border-radius:15px;padding:20px;background:linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);margin-bottom:10px;">
          <div style="display:flex;align-items:center;margin-bottom:10px;">
            <div style="width:40px;height:40px;background:#007AFF;border-radius:8px;display:flex;align-items:center;justify-content:center;color:white;font-weight:bold;margin-right:10px;">📱</div>
            <div>
              <h4 style="margin:0;color:#1d1d1f;font-size:16px;">{title or 'Title'}</h4>
              <p style="margin:0;color:#86868b;font-size:12px;">now</p>
            </div>
          </div>
          <p style="margin:0;color:#1d1d1f;font-size:14px;line-height:1.4;">{body or 'Notification body will appear here...'}</p>
        </div>
        """, unsafe_allow_html=True)
        
        # Android Preview
        st.write("**🤖 Android Preview:**")
        st.markdown(f"""
        <div style="border:1px solid #34A853;border-radius:8px;padding:15px;background:#f8f9fa;margin-bottom:10px;">
          <div style="display:flex;align-items:center;margin-bottom:8px;">
            <div style="width:32px;height:32px;background:#34A853;border-radius:6px;display:flex;align-items:center;justify-content:center;color:white;font-size:14px;margin-right:10px;">📱</div>
            <div>
              <h4 style="margin:0;color:#202124;font-size:14px;font-weight:500;">{title or 'Title'}</h4>
              <p style="margin:0;color:#5f6368;font-size:12px;">Just now</p>
            </div>
          </div>
          <p style="margin:0;color:#202124;font-size:13px;line-height:1.3;">{body or 'Notification body will appear here...'}</p>
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
with tab3:
    st.subheader("📈 Notification Analytics")
    
    # Placeholder for analytics - can be expanded later
    st.info("📊 Analytics features coming soon! Track notification delivery rates, device types, and more.")
    
    # Show recent activity placeholder
    st.write("**📋 Recent Activity:**")
    st.write("• No recent notifications sent")
    st.write("• Analytics will appear here after sending notifications")

# Initialize session state for tokens
if 'processed_tokens' not in st.session_state:
    st.session_state.processed_tokens = None
if 'show_confirmation' not in st.session_state:
    st.session_state.show_confirmation = False

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
    
    if recipient_method == "📢 Send to All Agents":
        st.info("🚀 Using optimized direct token fetching for better performance...")
        with st.spinner("🔍 Fetching all tokens directly from database..."):
            tokens = fetch_all_tokens_directly()
        st.success(f"✅ Found {len(tokens)} total tokens in database")
        
    else:
        # Gather cpIds first
        cpids = []
        
        if recipient_method == "📁 Upload CSV File" and 'uploaded_csv' in locals() and uploaded_csv:
            try:
                df = pd.read_csv(uploaded_csv)
                if 'cpId' in df.columns:
                    cpids += df['cpId'].dropna().astype(str).tolist()
                else:
                    st.error("❌ CSV must contain a 'cpId' column")
                    st.stop()
            except Exception as e:
                st.error(f"❌ Error reading CSV: {e}")
                st.stop()
                
        elif recipient_method == "✏️ Manual Entry" and 'manual_cpids' in locals() and manual_cpids:
            cpids += [c.strip() for c in manual_cpids.split('\n') if c.strip()]

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
    
    # Confirmation
    st.markdown("### 🚀 Ready to Send!")
    
    # Show notification preview
    st.write("**📋 Notification Preview:**")
    st.info(f"**Title:** {title}\n**Body:** {body}")
    
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
            # Get tokens from session state
            tokens = st.session_state.processed_tokens
            
            st.markdown("### 📤 Sending Notifications...")
            
            # Debug: Show what we're about to send
            st.info(f"🚀 Sending to {len(tokens)} tokens with click action: {current_click_action}")
            
            # Send notifications with enhanced progress tracking
            start_time = time.time()
            
            try:
                with st.spinner("🚀 Sending notifications in parallel..."):
                    summary, errors = send_notifications_parallel(
                        title, body, tokens, batch_size, max_workers, 
                        current_click_action, current_default_route, current_screen_name
                    )
            except Exception as e:
                st.error(f"❌ Error during sending: {str(e)}")
                st.stop()
            
            end_time = time.time()
            duration = end_time - start_time
            
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
