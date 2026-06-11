import os
import sys
import time
import json
import random
import logging
from pathlib import Path

import cv2
from dotenv import load_dotenv

import google.generativeai as genai
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

# ==========================================
# CONFIG & LOGGING SETUP
# ==========================================

# Setup Logging
SCRIPT_DIR = Path(__file__).parent.absolute()
LOG_FILE = SCRIPT_DIR / "youtube_uploader.log"

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, mode='a', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

# Load env variables
load_dotenv(SCRIPT_DIR / ".env")

MEDIA_BASE_FOLDER = os.getenv("MEDIA_BASE_FOLDER")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
YOUTUBE_CLIENT_ID = os.getenv("YOUTUBE_CLIENT_ID")
YOUTUBE_CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET")

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

def check_env_vars():
    required_vars = [
        "MEDIA_BASE_FOLDER", "GEMINI_API_KEY", "YOUTUBE_CLIENT_ID",
        "YOUTUBE_CLIENT_SECRET"
    ]
    missing = [var for var in required_vars if not os.getenv(var)]
    if missing:
        logging.error(f"Missing environment variables: {', '.join(missing)}")
        sys.exit(1)

# ==========================================
# HELPER FUNCTIONS
# ==========================================

def get_random_video():
    base_folder = Path(MEDIA_BASE_FOLDER)
    if not base_folder.exists() or not base_folder.is_dir():
        logging.error(f"Base folder does not exist: {base_folder}")
        sys.exit(1)
        
    videos_folder = base_folder / "videos"
    
    if not videos_folder.exists():
        logging.error("videos/ subfolder is missing in the base folder.")
        sys.exit(1)
        
    video_files = [f for f in videos_folder.iterdir() if f.suffix.lower() in ['.mp4', '.mov'] and f.is_file()]
    
    if not video_files:
        logging.error("No valid video files found in videos/ directory.")
        sys.exit(1)
        
    selected_video = random.choice(video_files)
    
    logging.info(f"Selected Video: {selected_video.name}")
    
    return selected_video

def extract_video_frame(video_path: Path) -> Path:
    temp_dir = SCRIPT_DIR / "temp"
    temp_dir.mkdir(exist_ok=True)
    frame_path = temp_dir / f"temp_frame_{int(time.time())}.jpg"
    
    logging.info("Extracting middle frame from video...")
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        logging.error(f"Failed to open video: {video_path}")
        raise Exception("Failed to open video file")
        
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    
    if total_frames <= 0 or fps <= 0:
        logging.warning("Could not determine frame count or FPS. Checking duration constraints...")
    else:
        duration_sec = total_frames / fps
        if duration_sec > 60:
            logging.warning(f"Video duration ({duration_sec:.1f}s) exceeds YouTube Shorts 60s limit.")
    
    # Seek to middle
    mid_frame = total_frames // 2
    cap.set(cv2.CAP_PROP_POS_FRAMES, mid_frame)
    
    ret, frame = cap.read()
    if not ret:
        logging.error("Failed to read the middle frame from the video.")
        cap.release()
        raise Exception("Failed to read video frame")
        
    # Check aspect ratio
    height, width, _ = frame.shape
    aspect_ratio = width / height
    if abs(aspect_ratio - (9 / 16)) > 0.05:
        logging.warning(f"Video aspect ratio is {aspect_ratio:.2f}. YouTube Shorts prefers 9:16 (0.56).")
        
    cv2.imwrite(str(frame_path), frame)
    cap.release()
    logging.info(f"Saved extracted frame to {frame_path.name}")
    
    return frame_path

def strip_markdown(text: str) -> str:
    text = text.strip()
    if text.startswith("```json"):
        text = text[len("```json"):].strip()
    elif text.startswith("```"):
        text = text[3:].strip()
        
    if text.endswith("```"):
        text = text[:-3].strip()
    return text

def generate_content_with_gemini(frame_path: Path):
    logging.info("Generating content using Gemini 2.5 Flash...")
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-2.5-flash')
    
    def try_gemini(prompt, file_path):
        mime_type = "image/jpeg"
        if file_path.suffix.lower() == '.png': mime_type = "image/png"
        elif file_path.suffix.lower() == '.webp': mime_type = "image/webp"
            
        with open(file_path, "rb") as f:
            image_data = f.read()
            
        parts = [
            {"mime_type": mime_type, "data": image_data},
            prompt
        ]
        
        try:
            response = model.generate_content(parts)
            return json.loads(strip_markdown(response.text))
        except Exception as e:
            logging.warning(f"Gemini call failed: {str(e)}. Retrying in 5 seconds...")
            time.sleep(5)
            response = model.generate_content(parts)
            return json.loads(strip_markdown(response.text))
            
    yt_prompt = (
        "You are a viral social media content expert. Based on this video frame, generate: "
        "1) A punchy, engaging YouTube Shorts title (max 80 characters, must end with #Shorts), "
        "2) A YouTube Shorts description (2-3 sentences, include 5-7 highly relevant and viral hashtags at the end). "
        "Return ONLY a JSON object with keys: 'youtube_title' and 'youtube_description'. No other text."
    )
    
    logging.info("Calling Gemini for YouTube metadata...")
    yt_data = try_gemini(yt_prompt, frame_path)
    
    return yt_data

# ==========================================
# YOUTUBE UPLOAD
# ==========================================

def get_youtube_service():
    creds = None
    token_file = SCRIPT_DIR / "youtube_token.json"
    
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
        
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            client_config = {
                "installed": {
                    "client_id": YOUTUBE_CLIENT_ID,
                    "client_secret": YOUTUBE_CLIENT_SECRET,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": ["http://localhost"]
                }
            }
            flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
            creds = flow.run_local_server(port=0)
            
        with open(token_file, "w") as token:
            token.write(creds.to_json())
            
    return build('youtube', 'v3', credentials=creds)

def upload_to_youtube(video_path: Path, yt_data: dict):
    logging.info("Starting YouTube Upload...")
    youtube = get_youtube_service()
    
    title = yt_data.get('youtube_title', 'Viral Short #Shorts')
    description = yt_data.get('youtube_description', '')
    
    # Extract tags from description (words starting with #)
    tags = [word.strip('#') for word in description.split() if word.startswith('#')]
    
    body = {
        'snippet': {
            'title': title,
            'description': description,
            'tags': tags,
            'categoryId': '22' # People & Blogs
        },
        'status': {
            'privacyStatus': 'public',
            'selfDeclaredMadeForKids': False
        }
    }
    
    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True)
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media
    )
    
    response = None
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                logging.info(f"YouTube Upload {int(status.progress() * 100)}% complete.")
        except HttpError as e:
            logging.error(f"YouTube HTTP Error occurred: {e}")
            raise
    
    video_id = response.get('id')
    logging.info(f"YouTube upload complete! Video ID: {video_id} - URL: https://youtu.be/{video_id}")
    
    logging.info("Polling YouTube for processing status (max 10 minutes)...")
    start_time = time.time()
    while time.time() - start_time < 600:
        status_req = youtube.videos().list(
            part="processingDetails",
            id=video_id
        )
        status_res = status_req.execute()
        
        if not status_res.get('items'):
            logging.warning("Video not found yet, retrying...")
        else:
            processing_status = status_res['items'][0].get('processingDetails', {}).get('processingStatus')
            if processing_status == 'succeeded':
                logging.info("YouTube video processing succeeded!")
                return
            elif processing_status == 'failed':
                logging.error("YouTube video processing failed!")
                raise Exception("YouTube Processing Failed")
        
        time.sleep(10)
        
    logging.warning("YouTube processing poll timed out after 10 minutes, assuming success.")

# ==========================================
# MAIN EXECUTION
# ==========================================

def main():
    logging.info("=== ViralFlow YouTube Uploader Started ===")
    
    try:
        check_env_vars()
        
        # Step 1: Select files
        video_path = get_random_video()
        
        # Step 2: Extract frame
        frame_path = extract_video_frame(video_path)
        
        # Step 3: Gemini Content
        yt_data = generate_content_with_gemini(frame_path)
        logging.info(f"Generated YouTube Data: {yt_data}")
        
        yt_failed = False
        
        # Step 4: YouTube Upload
        try:
            upload_to_youtube(video_path, yt_data)
        except Exception as e:
            logging.error(f"YouTube upload encountered an exception: {e}", exc_info=True)
            yt_failed = True
            
        if yt_failed:
            logging.error("YouTube failed. Aborting cleanup.")
            sys.exit(1)
            
        # Step 5: Cleanup
        logging.info("Upload succeeded! Starting cleanup...")
        try:
            video_path.unlink()
            frame_path.unlink()
            logging.info("Cleanup complete. Files deleted.")
        except Exception as e:
            logging.error(f"Failed during cleanup: {e}", exc_info=True)
            sys.exit(1)
            
        logging.info("=== ViralFlow YouTube Uploader Completed Successfully ===")
        sys.exit(0)
        
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
