import os
import streamlit as st

# Set page config first, before any other Streamlit commands
st.set_page_config(
    page_title="ACN FCM Notification Sender",
    page_icon="📱",
    layout="wide"
)

import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore, messaging, storage
from firebase_admin.exceptions import FirebaseError
import time
from typing import List, Union
import sys
import urllib.parse
from google.cloud import storage as google_storage
import requests

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
            firebase_admin.initialize_app(cred, {'storageBucket': 'acn-resale-inventories-dde03.firebasestorage.app'})
        return firestore.client(), storage.bucket()
    except Exception as e:
        st.error(f"Failed to initialize Firebase: {str(e)}")
        st.stop()

try:
    db, bucket = init_firebase()
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

def list_storage_files(folder=""):
    """List all files in a Firebase Storage folder."""
    files = [""]  # Add empty option
    try:
        blobs = bucket.list_blobs(prefix=folder)
        for blob in blobs:
            # Only include actual files, not folders
            if not blob.name.endswith('/') and blob.name != folder:
                files.append(blob.name)
    except Exception as e:
        st.error(f"Error listing storage files: {str(e)}")
    return files

def get_proper_storage_url(file_path):
    """Get the proper Firebase Storage download URL."""
    try:
        blob = bucket.blob(file_path)
        
        # First ensure the file exists
        if not blob.exists():
            return None, f"File {file_path} does not exist"
        
        # Method 1: Try to get public URL (if blob is public)
        try:
            blob.make_public()
            public_url = f"https://firebasestorage.googleapis.com/v0/b/{bucket.name}/o/{urllib.parse.quote(file_path, safe='')}?alt=media"
            
            # Test if URL is accessible
            test_response = requests.head(public_url, timeout=5)
            if test_response.status_code == 200:
                return public_url, "Public URL"
            
        except Exception as e:
            st.warning(f"Could not make {file_path} public: {e}")
        
        # Method 2: Generate signed URL (fallback)
        from datetime import datetime, timedelta
        expiration = datetime.utcnow() + timedelta(days=365)
        signed_url = blob.generate_signed_url(expiration, version="v4")
        
        # Test signed URL
        test_response = requests.head(signed_url, timeout=5)
        if test_response.status_code == 200:
            return signed_url, "Signed URL"
        else:
            return None, f"Signed URL test failed with status {test_response.status_code}"
            
    except Exception as e:
        return None, f"Error getting URL: {str(e)}"

def upload_file_to_storage(file, folder="test"):
    """Upload a file to Firebase Storage and return the proper download URL."""
    try:
        file_path = f"{folder}/{file.name}"
        blob = bucket.blob(file_path)
        
        # Determine content type based on file extension
        file_ext = file.name.split('.')[-1].lower()
        content_type_map = {
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg', 
            'png': 'image/png',
            'gif': 'image/gif',
            'mp3': 'audio/mpeg',
            'wav': 'audio/wav',
            'ogg': 'audio/ogg',
            'm4a': 'audio/mp4'
        }
        
        blob.content_type = content_type_map.get(file_ext, "application/octet-stream")
        
        # Reset file pointer to beginning
        file.seek(0)
        
        # Upload the file
        blob.upload_from_file(file, content_type=blob.content_type)
        st.success(f"✅ Uploaded {file.name}")
        
        # Get proper download URL
        download_url, url_type = get_proper_storage_url(file_path)
        if download_url:
            st.success(f"✅ Got {url_type} for {file.name}")
            st.info(f"🔗 URL: {download_url}")
            return download_url
        else:
            st.error(f"❌ Could not get accessible URL: {url_type}")
            return None
        
    except Exception as e:
        st.error(f"Error uploading {file.name}: {str(e)}")
        return None

def test_url_accessibility(url):
    """Test if a URL is accessible and return details."""
    try:
        response = requests.head(url, timeout=10, allow_redirects=True)
        content_type = response.headers.get('content-type', 'unknown')
        content_length = response.headers.get('content-length', 'unknown')
        
        return {
            'accessible': response.status_code == 200,
            'status_code': response.status_code,
            'content_type': content_type,
            'content_length': content_length,
            'final_url': response.url
        }
    except Exception as e:
        return {
            'accessible': False,
            'error': str(e)
        }

def send_notifications(title, body, tokens, batch_size=100, image_url=None, sound_url=None, sound_type="default"):
    """
    Send notifications with proper audio handling.
    
    Args:
        sound_type: "default", "bundled", or "custom"
        - "default": Use system default sound
        - "bundled": Use app-bundled sound file (sound_url should be filename)
        - "custom": Send sound URL in data for app-side handling
    """
    summary = {"success": 0, "pruned": 0, "errors": 0}
    errors_list = []

    st.info(f"📸 Image URL: {image_url or 'None'}")
    # st.info(f"🔊 Sound: {sound_type} - {sound_url or 'Default'}")
    
    # Test URLs before sending
    if image_url:
        test_result = test_url_accessibility(image_url)
        if test_result['accessible']:
            st.success(f"✅ Image URL is accessible ({test_result['content_type']})")
        else:
            st.error(f"❌ Image URL is not accessible: {test_result.get('error', 'HTTP ' + str(test_result.get('status_code', 'Unknown')))}")
            
    # if sound_url and sound_type == "custom":
    #     test_result = test_url_accessibility(sound_url)
    #     if test_result['accessible']:
    #         st.success(f"✅ Sound URL is accessible ({test_result['content_type']})")
    #     else:
    #         st.error(f"❌ Sound URL is not accessible: {test_result.get('error', 'HTTP ' + str(test_result.get('status_code', 'Unknown')))}")

    for i in range(0, len(tokens), batch_size):
        batch = tokens[i:i+batch_size]
        for doc_ref, token, is_array in batch:
            try:
                # Determine sound configuration based on type
                # if sound_type == "bundled" and sound_url:
                #     # Extract filename from URL or use as-is if it's already a filename
                #     sound_filename = sound_url.split('/')[-1] if '/' in sound_url else sound_url
                #     # Remove extension for iOS (iOS expects filename without extension)
                #     ios_sound = sound_filename.rsplit('.', 1)[0] + '.wav'
                #     android_sound = sound_filename
                # elif sound_type == "custom":
                #     # Use default sound but send URL in data for app handling
                #     ios_sound = "default"
                #     android_sound = "default"
                # else:
                #     # Default system sound
                ios_sound = "default"
                android_sound = "default"

                # Enhanced data payload for app-side handling
                data_payload = {
                    "title": title,
                    "body": body,
                    "click_action": "FLUTTER_NOTIFICATION_CLICK",
                    "type": "rich_notification",
                    # "sound_type": sound_type
                }
                
                # Add media URLs to data if available
                if image_url:
                    data_payload["image_url"] = image_url
                # if sound_url:
                #     data_payload["sound_url"] = sound_url
                #     if sound_type == "bundled":
                #         data_payload["sound_filename"] = sound_filename

                # Method 1: Send data-only notification for custom audio handling
                # if sound_type == "custom" and sound_url:
                #     data_only_payload = {
                #         **data_payload,
                #         "notification_type": "silent_with_custom_audio",
                #         "custom_audio_url": sound_url
                #     }
                    
                #     data_message = messaging.Message(
                #         token=token,
                #         data=data_only_payload,
                #         android=messaging.AndroidConfig(
                #             priority="high",
                #             data=data_only_payload
                #         ),
                #         apns=messaging.APNSConfig(
                #             payload=messaging.APNSPayload(
                #                 aps=messaging.Aps(
                #                     content_available=True,
                #                     mutable_content=True
                #                 ),
                #                 custom_data=data_only_payload
                #             )
                #         )
                #     )
                    
                #     # Send data-only message first
                #     data_response = messaging.send(data_message)
                #     st.write(f"📡 Sent data notification for custom audio: {token[:8]}...")
                    
                #     # Small delay to ensure data message is processed first
                #     time.sleep(0.1)

                # Method 2: Send display notification with proper sound configuration
                notification = messaging.Notification(
                    title=title,
                    body=body,
                    image=image_url if image_url else None
                )

                # Android configuration
                android_notification = messaging.AndroidNotification(
                    title=title,
                    body=body,
                    image=image_url if image_url else None,
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

                # iOS configuration
                aps_alert = messaging.ApsAlert(
                    title=title,
                    body=body
                )

                aps = messaging.Aps(
                    alert=aps_alert,
                    sound=ios_sound,
                    badge=1,
                    mutable_content=True,
                    content_available=True,
                    category="RICH_NOTIFICATION"
                )

                apns_payload = messaging.APNSPayload(aps=aps)
                if image_url:
                    # React Native specific payload structure for both foreground and background
                    apns_payload.custom_data = {
                        "notification": {
                            "title": title,
                            "body": body,
                            "image": image_url,
                            "android": {
                                "imageUrl": image_url,
                                "priority": "high",
                                "channelId": "high_importance_channel"
                            },
                            "ios": {
                                "imageUrl": image_url,
                                "attachments": [{
                                    "url": image_url,
                                    "type": "image"
                                }]
                            }
                        },
                        "data": {
                            "title": title,
                            "body": body,
                            "image": image_url,
                            "imageUrl": image_url,
                            "type": "rich_notification",
                            "foreground": True,
                            "background": True
                        }
                    }
                else:
                    apns_payload.custom_data = {
                        "notification": {
                            "title": title,
                            "body": body
                        },
                        "data": {
                            "title": title,
                            "body": body,
                            "type": "rich_notification"
                        }
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

                # Create the main message
                message = messaging.Message(
                    token=token,
                    notification=notification,
                    android=android_config,
                    apns=apns_config,
                    data=data_payload
                )

                # Send the main notification
                response = messaging.send(message)
                st.write(f"📱 Sent notification to: {token[:8]}... (ID: {response})")
                summary["success"] += 1
                
            except FirebaseError as e:
                error_code = getattr(e, 'code', '')
                error_message = str(e).lower()
                
                if ('registration-token' in error_code or 
                    error_code == 'NOT_FOUND' or 
                    'invalid-registration-token' in error_message or
                    'unregistered' in error_message):
                    # Prune invalid token
                    try:
                        if is_array:
                            doc_ref.update({"fsmToken": firestore.ArrayRemove([token])})
                            st.write(f"🗑️ Removed expired token {token[:8]}... from array.")
                        else:
                            doc_ref.update({"fsmToken": firestore.DELETE_FIELD})
                            st.write(f"🗑️ Deleted fsmToken field (single token).")
                        summary["pruned"] += 1
                    except Exception as prune_error:
                        st.warning(f"⚠️ Could not prune token {token[:8]}...: {prune_error}")
                        summary["errors"] += 1
                else:
                    st.warning(f"⚠️ FCM error for {token[:8]}...: {e}")
                    errors_list.append((token, str(e)))
                    summary["errors"] += 1
                    
            except Exception as e:
                st.error(f"❌ Unexpected error for {token[:8]}...: {e}")
                errors_list.append((token, str(e)))
                summary["errors"] += 1

        # Prevent rate limiting
        time.sleep(0.2)

    return summary, errors_list

# --- Streamlit UI ---

st.title("📱 ACN Agent FCM Notification Sender")

st.markdown(""" 
Send rich notifications with images and custom sounds to your agents.
Upload CSV files, enter cpIds manually, or send to all agents.
Invalid tokens are automatically pruned from the database.
""")

# Configuration
batch_size = 100  # Default batch size
storage_folder = "test"  # Default storage folder

# Main interface
tab1, tab2, tab3 = st.tabs(["📝 Compose", "📊 Recipients", "🎨 Media"])

with tab2:
    st.subheader("📊 Select Recipients")
    
    # Send to all agents option
    send_to_all = st.checkbox("📢 Send to all agents", 
                             help="Send notifications to all agents in the database")
    
    if send_to_all:
        st.info("📋 Will fetch all agent cpIds from the database")
    else:
        col1, col2 = st.columns(2)
        
        with col1:
            st.write("**📁 Upload CSV File**")
            uploaded_csv = st.file_uploader(
                "Select CSV file with cpId column", 
                type="csv",
                help="Upload a CSV file containing a 'cpId' column"
            )
            
            if uploaded_csv:
                try:
                    df = pd.read_csv(uploaded_csv)
                    st.write("📋 CSV Preview:")
                    st.dataframe(df.head())
                    
                    if 'cpId' in df.columns:
                        cpid_count = len(df['cpId'].dropna())
                        st.success(f"✅ Found {cpid_count} cpIds in CSV")
                    else:
                        st.error("❌ CSV must contain a 'cpId' column")
                        st.write("Available columns:", list(df.columns))
                except Exception as e:
                    st.error(f"❌ Error reading CSV: {str(e)}")
        
        with col2:
            st.write("**✏️ Manual Entry**")
            manual_cpids = st.text_area(
                "Enter cpIds (one per line)",
                height=150,
                help="Enter cpIds separated by newlines for multiple recipients",
                placeholder="12345\n67890\n11111"
            )
            
            if manual_cpids:
                cpid_list = [cpid.strip() for cpid in manual_cpids.split('\n') if cpid.strip()]
                st.info(f"📝 {len(cpid_list)} cpIds entered manually")

with tab3:
    st.subheader("🎨 Media Files")
    
    # Get available files first
    available_files = list_storage_files(storage_folder)
    
    # Initialize variables
    uploaded_image = None
    selected_image = None
    # uploaded_sound = None
    # selected_sound = None
    # sound_type = "default"
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.write("**🖼️ Image**")
        
        # Upload new image
        uploaded_image = st.file_uploader(
            "Upload new image", 
            type=["jpg", "jpeg", "png", "gif"],
            help="Upload an image file for the notification"
        )
        
        if uploaded_image:
            st.image(uploaded_image, caption="Preview", width=200)
        
        # Select from existing images
        image_files = [""] + [f for f in available_files if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif'))]
        
        selected_image = st.selectbox(
            "Or select existing image", 
            image_files,
            help="Choose from images already uploaded to Firebase Storage"
        )
        
        if selected_image:
            # Get proper URL
            image_url, url_type = get_proper_storage_url(selected_image)
            if image_url:
                st.success(f"✅ Got {url_type}")
                st.code(image_url)
                
                # Test the URL
                result = test_url_accessibility(image_url)
                if result['accessible']:
                    st.success("✅ Image URL is accessible")
                else:
                    st.error("❌ Image URL is not accessible")
            else:
                st.error(f"❌ Could not get URL: {url_type}")
    
    with col2:
        st.write("**🔊 Sound**")
        st.info("Audio functionality is currently disabled")
        
        # # Sound type selection
        # sound_type = st.radio(
        #     "Sound Type",
        #     ["default", "bundled", "custom"],
        #     help="""
        #     • Default: Use system notification sound
        #     • Bundled: Use sound file bundled with your app
        #     • Custom: Send URL to app for custom handling
        #     """
        # )
        
        # uploaded_sound = None
        # selected_sound = None
        
        # if sound_type != "default":
        #     # Upload new sound
        #     uploaded_sound = st.file_uploader(
        #         "Upload new sound", 
        #         type=["mp3", "wav", "ogg", "m4a"],
        #         help="Upload a sound file for the notification"
        #     )
        
        # Audio instructions
        with st.expander("🔊 Audio Implementation Guide", expanded=False):
            st.markdown("""
            **📱 Audio functionality is currently disabled**
            
            This feature will be re-enabled in a future update.
            """)

with tab1:
    st.subheader("📝 Notification Content")
    
    # Notification title and body
    title = st.text_input(
        "📌 Notification Title", 
        placeholder="Enter notification title",
        max_chars=100
    ).strip()
    
    body = st.text_area(
        "📄 Notification Body", 
        placeholder="Enter notification message",
        height=100,
        max_chars=500
    ).strip()
    
    # Character count indicators
    if title:
        st.caption(f"Title: {len(title)}/100 characters")
    if body:
        st.caption(f"Body: {len(body)}/500 characters")
    
    # Preview section
    if title or body:
        st.subheader("👀 Preview")
        with st.container():
            st.markdown(f"""
            <div style="border: 1px solid #ddd; border-radius: 10px; padding: 15px; background-color: #f9f9f9;">
                <h4 style="margin: 0 0 10px 0;">{title or "Title"}</h4>
                <p style="margin: 0;">{body or "Notification body will appear here..."}</p>
            </div>
            """, unsafe_allow_html=True)

# Send button and main logic
st.markdown("---")
col1, col2, col3 = st.columns([1, 2, 1])

with col2:
    send_button = st.button(
        "🚀 Send Notifications", 
        type="primary", 
        use_container_width=True,
        help="Send notifications to selected recipients"
    )

if send_button:
    # Validation
    if not title:
        st.error("❌ Notification title cannot be empty.")
        st.stop()
    
    if not body:
        st.error("❌ Notification body cannot be empty.")
        st.stop()
    
    # Collect cpIds from various sources
    cpids = []
    
    if send_to_all:
        with st.spinner("📥 Fetching all cpIds from database..."):
            cpids = fetch_all_cpids()
        st.success(f"✅ Found {len(cpids)} unique cpIds in the database.")
    else:
        # Get cpIds from CSV
        if 'uploaded_csv' in locals() and uploaded_csv:
            try:
                df = pd.read_csv(uploaded_csv)
                if 'cpId' in df.columns:
                    csv_cpids = df['cpId'].dropna().astype(str).tolist()
                    cpids.extend(csv_cpids)
                    st.success(f"✅ Loaded {len(csv_cpids)} cpIds from CSV.")
                else:
                    st.error("❌ CSV must contain a 'cpId' column.")
                    st.stop()
            except Exception as e:
                st.error(f"❌ Error reading CSV: {str(e)}")
                st.stop()
        
        # Get cpIds from manual input
        if 'manual_cpids' in locals() and manual_cpids:
            manual_list = [cpid.strip() for cpid in manual_cpids.split('\n') if cpid.strip()]
            cpids.extend(manual_list)
            st.success(f"✅ Added {len(manual_list)} cpIds from manual input.")
    
    if not cpids:
        st.error("❌ Please provide cpIds through one of the available methods.")
        st.stop()
    
    # Remove duplicates and clean data
    cpids = list(set(filter(None, cpids)))
    st.info(f"📝 Processing {len(cpids)} unique cpIds...")
    
    # Handle media uploads and get URLs
    final_image_url = None
    final_sound_url = None
    
    # Process image - check if variables exist and have values
    if 'uploaded_image' in locals() and uploaded_image:
        with st.spinner("⬆️ Uploading new image..."):
            final_image_url = upload_file_to_storage(uploaded_image, storage_folder)
    elif 'selected_image' in locals() and selected_image:
        final_image_url, url_type = get_proper_storage_url(selected_image)
        if final_image_url:
            st.info(f"📷 Using existing image ({url_type}): {selected_image}")
        else:
            st.error(f"❌ Could not get image URL: {url_type}")
            st.stop()
    
    # Process sound - get sound_type from locals or default
    # current_sound_type = locals().get('sound_type', 'default')
    current_sound_type = "default"
    
    # if current_sound_type != "default":
    #     if 'uploaded_sound' in locals() and uploaded_sound:
    #         with st.spinner("⬆️ Uploading new sound..."):
    #             final_sound_url = upload_file_to_storage(uploaded_sound, storage_folder)
    #     elif 'selected_sound' in locals() and selected_sound:
    #         final_sound_url, url_type = get_proper_storage_url(selected_sound)
    #         if final_sound_url:
    #             st.info(f"🔊 Using existing sound ({current_sound_type}, {url_type}): {selected_sound}")
    #         else:
    #             st.error(f"❌ Could not get sound URL: {url_type}")
    #             if current_sound_type == "bundled":
    #                 st.error("❌ Bundled sound requires valid sound file")
    #                 st.stop()
    #     elif current_sound_type == "bundled":
    #         st.error("❌ Bundled sound type requires a sound file selection")
    #         st.stop()
    # else:
    st.info("🔊 Using default system notification sound")
    
    # Fetch tokens
    with st.spinner("🔍 Fetching notification tokens..."):
        tokens = fetch_tokens_for_cpids(cpids)
    
    if not tokens:
        st.warning("⚠️ No valid tokens found for the provided cpIds. Nothing to send.")
        st.stop()
    
    st.success(f"✅ Found {len(tokens)} valid tokens.")
    
    # Send notifications
    st.markdown("### 📤 Sending Notifications...")
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    with st.spinner("🚀 Sending notifications..."):
        summary, errors = send_notifications(
            title=title, 
            body=body, 
            tokens=tokens, 
            batch_size=batch_size,
            image_url=final_image_url, 
            # sound_url=final_sound_url,
            # sound_type=current_sound_type
        )
    
    progress_bar.progress(100)
    status_text.text("✅ Notification sending completed!")
    
    # Display comprehensive results
    st.markdown("---")
    st.subheader("📊 Results Summary")
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric("✅ Sent Successfully", summary['success'])
    with col2:
        st.metric("🗑️ Tokens Pruned", summary['pruned'])
    with col3:
        st.metric("❌ Errors", summary['errors'])
    with col4:
        success_rate = (summary['success'] / len(tokens) * 100) if tokens else 0
        st.metric("📈 Success Rate", f"{success_rate:.1f}%")
    
    # Show errors if any
    if summary['errors'] > 0:
        with st.expander("❌ View Error Details", expanded=False):
            for token, error in errors:
                st.write(f"🔴 Token {token[:8]}...: {error}")
    
    # Final status
    if summary['success'] > 0:
        st.success(f"🎉 Successfully sent {summary['success']} notifications!")
        
        if final_image_url:
            st.info("📸 Notifications included custom image")
        st.info("🔊 Notifications used default system sound")
        # if current_sound_type == "default":
        #     st.info("🔊 Notifications used default system sound")
        # elif current_sound_type == "bundled" and final_sound_url:
        #     st.info("🔊 Notifications referenced bundled app sound")
        # elif current_sound_type == "custom" and final_sound_url:
        #     st.info("🔊 Notifications included custom sound URL for app handling")
    else:
        st.error("❌ No notifications were sent successfully.")

# Footer
st.markdown("---")
st.markdown("""
<div style="text-align: center; color: #666;">
    <p>📱 ACN Agent FCM Notification Sender | Built with Streamlit & Firebase</p>
</div>
""", unsafe_allow_html=True)