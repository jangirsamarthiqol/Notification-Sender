# Firebase Environment Variables Setup

Your NotificationSender app requires Firebase environment variables to connect to your Firebase project. Follow these steps to set them up:

## Step 1: Create a .env file

Create a file named `.env` in your project root directory with the following content:

```env
# Firebase Configuration
# Fill in your actual Firebase service account values

# Firebase Service Account Type
FIREBASE_TYPE=service_account

# Your Firebase Project ID
FIREBASE_PROJECT_ID=your-project-id

# Private Key ID from Firebase service account
FIREBASE_PRIVATE_KEY_ID=your-private-key-id

# Private Key from Firebase service account (keep the quotes and \n characters)
FIREBASE_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\nYOUR_PRIVATE_KEY_HERE\n-----END PRIVATE KEY-----\n"

# Client Email from Firebase service account
FIREBASE_CLIENT_EMAIL=your-service-account@your-project-id.iam.gserviceaccount.com

# Client ID from Firebase service account
FIREBASE_CLIENT_ID=your-client-id

# Auth URI (usually this value)
FIREBASE_AUTH_URI=https://accounts.google.com/o/oauth2/auth

# Token URI (usually this value)
FIREBASE_TOKEN_URI=https://oauth2.googleapis.com/token

# Auth Provider Certificate URL (usually this value)
FIREBASE_AUTH_PROVIDER_CERT_URL=https://www.googleapis.com/oauth2/v1/certs

# Client Certificate URL (usually this value)
FIREBASE_CLIENT_CERT_URL=https://www.googleapis.com/robot/v1/metadata/x509/your-service-account%40your-project-id.iam.gserviceaccount.com
```

## Step 2: Get Firebase Service Account Credentials

1. Go to the [Firebase Console](https://console.firebase.google.com/)
2. Select your project
3. Go to Project Settings (gear icon) → Service Accounts
4. Click "Generate new private key"
5. Download the JSON file

## Step 3: Extract Values from the JSON

From the downloaded JSON file, copy these values to your `.env` file:

- `type` → `FIREBASE_TYPE`
- `project_id` → `FIREBASE_PROJECT_ID`
- `private_key_id` → `FIREBASE_PRIVATE_KEY_ID`
- `private_key` → `FIREBASE_PRIVATE_KEY` (keep the quotes and \n characters)
- `client_email` → `FIREBASE_CLIENT_EMAIL`
- `client_id` → `FIREBASE_CLIENT_ID`
- `auth_uri` → `FIREBASE_AUTH_URI`
- `token_uri` → `FIREBASE_TOKEN_URI`
- `auth_provider_x509_cert_url` → `FIREBASE_AUTH_PROVIDER_CERT_URL`
- `client_x509_cert_url` → `FIREBASE_CLIENT_CERT_URL`

## Step 4: Important Notes

- **Keep the .env file secure** - never commit it to version control
- The `FIREBASE_PRIVATE_KEY` should include the full private key with quotes and `\n` characters
- Replace `your-service-account@your-project-id.iam.gserviceaccount.com` with your actual service account email
- The `FIREBASE_CLIENT_CERT_URL` should use URL encoding for the @ symbol (%40)

## Step 5: Test the Setup

After setting up your `.env` file, run your Streamlit app:

```bash
streamlit run NotificationSender.py
```

The app should now load without the "Missing required environment variables" error.

## Security Note

Make sure to add `.env` to your `.gitignore` file to prevent accidentally committing your Firebase credentials to version control.

