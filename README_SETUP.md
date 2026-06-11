# ViralFlow Setup Guide

ViralFlow automates uploading content to YouTube Shorts and Instagram (Feed + Reels) simultaneously using AI for captions and metadata.

## Prerequisites
1. Python 3.9+ installed and added to your system PATH.
2. A local folder containing two subfolders: `videos/` and `images/`

## Step 1: Install Dependencies
Open a command prompt in this folder and run:
```bash
# Optional: Create a virtual environment
python -m venv venv
venv\Scripts\activate

# Install requirements
pip install -r requirements.txt
```

## Step 2: Configure Environment Variables
Rename `.env.example` to `.env` and fill in the values:

- `MEDIA_BASE_FOLDER`: The full path to the folder containing your `videos/` and `images/` folders (e.g., `C:\Users\Name\Desktop\Content`).
- `GEMINI_API_KEY`: Get this from Google AI Studio.
- `YOUTUBE_CLIENT_ID` & `YOUTUBE_CLIENT_SECRET`: Create an OAuth 2.0 Client ID for a Desktop application in Google Cloud Console. Enable the YouTube Data API v3.
- `IG_USER_ID`: Your Instagram Business Account numeric ID.
- `IG_ACCESS_TOKEN`: A long-lived access token from Facebook Developers for the Instagram Graph API.
- `IMGBB_API_KEY`: Get a free API key from https://api.imgbb.com/.

## Step 3: Run the Script
- The first time you run it, a browser window will open to authenticate your Google Account for YouTube upload access. It will save `youtube_token.json` for future runs.
- Double-click `run_uploader.bat` from your Desktop (you can move the `.bat` file to your desktop, but make sure to update its path inside the script, OR just create a shortcut of the `.bat` file on your desktop and leave the actual `.bat` file here).

### Note on Desktop Shortcut:
The easiest way to put this on your desktop is:
1. Right-click `run_uploader.bat` in this folder.
2. Select "Send to" -> "Desktop (create shortcut)".
