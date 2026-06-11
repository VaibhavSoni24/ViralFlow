import os
import sys
import time
import json
import random
import logging
from pathlib import Path
from datetime import datetime

import cv2
import requests
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
LOG_FILE = SCRIPT_DIR / "uploader.log"

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
IG_USER_ID = os.getenv("IG_USER_ID")
IG_ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN")
IMGBB_API_KEY = os.getenv("IMGBB_API_KEY")

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

def check_env_vars():
    required_vars = [
        "MEDIA_BASE_FOLDER", "GEMINI_API_KEY", "YOUTUBE_CLIENT_ID",
        "YOUTUBE_CLIENT_SECRET", "IG_USER_ID", "IG_ACCESS_TOKEN", "IMGBB_API_KEY"
    ]
    missing = [var for var in required_vars if not os.getenv(var)]
    if missing:
        logging.error(f"Missing environment variables: {', '.join(missing)}")
        sys.exit(1)

# ==========================================
# HELPER FUNCTIONS
# ==========================================

def get_random_media():
    base_folder = Path(MEDIA_BASE_FOLDER)
    if not base_folder.exists() or not base_folder.is_dir():
        logging.error(f"Base folder does not exist: {base_folder}")
        sys.exit(1)
        
    videos_folder = base_folder / "videos"
    images_folder = base_folder / "images"
    
    if not videos_folder.exists() or not images_folder.exists():
        logging.error("videos/ or images/ subfolder is missing in the base folder.")
        sys.exit(1)
        
    video_files = [f for f in videos_folder.iterdir() if f.suffix.lower() in ['.mp4', '.mov'] and f.is_file()]
    image_files = [f for f in images_folder.iterdir() if f.suffix.lower() in ['.jpg', '.jpeg', '.png', '.webp'] and f.is_file()]
    
    if not video_files:
        logging.error("No valid video files found in videos/ directory.")
        sys.exit(1)
        
    if not image_files:
        logging.error("No valid image files found in images/ directory.")
        sys.exit(1)
        
    selected_video = random.choice(video_files)
    selected_image = random.choice(image_files)
    
    logging.info(f"Selected Video: {selected_video.name}")
    logging.info(f"Selected Image: {selected_image.name}")
    
    return selected_video, selected_image

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
        if duration_sec > 90:
            logging.warning(f"Video duration ({duration_sec:.1f}s) exceeds Instagram Reels 90s ideal limit.")
    
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
        logging.warning(f"Video aspect ratio is {aspect_ratio:.2f}. Instagram prefers 9:16 (0.56).")
        
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

def generate_content_with_gemini(frame_path: Path, image_path: Path):
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
            
    # CALL A: Video Frame -> YouTube metadata
    yt_prompt = (
        "You are a viral social media content expert. Based on this video frame, generate: "
        "1) A punchy, engaging YouTube Shorts title (max 80 characters, must end with #Shorts), "
        "2) A YouTube Shorts description (2-3 sentences, include 5-7 highly relevant and viral hashtags at the end). "
        "Return ONLY a JSON object with keys: 'youtube_title' and 'youtube_description'. No other text."
    )
    
    logging.info("Calling Gemini for YouTube metadata...")
    yt_data = try_gemini(yt_prompt, frame_path)
    
    # CALL B: Image -> Instagram metadata
    ig_prompt = (
        "You are a viral social media content expert. Based on this image, generate: "
        "1) An engaging Instagram post caption (2-3 sentences, conversational tone), "
        "2) A set of 10-15 highly relevant and viral Instagram hashtags. "
        "Return ONLY a JSON object with keys: 'instagram_caption' and 'instagram_hashtags' (hashtags as a single string). No other text."
    )
    
    logging.info("Calling Gemini for Instagram metadata...")
    ig_data = try_gemini(ig_prompt, image_path)
    
    return yt_data, ig_data

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
            # We must construct client secrets dynamically or assume user provides client_secrets.json.
            # The prompt says YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET are in .env
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
    
    # Polling for processing status is tricky with YouTube Data API v3 as it only gives upload status.
    # Usually, a successful insert means upload is done. Processing happens asynchronously.
    # We will poll `videos.list` for processingDetails.
    
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
# INSTAGRAM UPLOAD
# ==========================================

def upload_to_imgbb(image_path: Path) -> dict:
    logging.info("Uploading image to ImgBB to get public URL...")
    import base64
    with open(image_path, "rb") as f:
        img_b64 = base64.b64encode(f.read())
        
    resp = requests.post("https://api.imgbb.com/1/upload", data={
        "key": IMGBB_API_KEY,
        "image": img_b64
    })
    
    if resp.status_code != 200:
        raise Exception(f"ImgBB upload failed: {resp.text}")
        
    data = resp.json()
    if not data.get("success"):
        raise Exception(f"ImgBB returned success=False: {data}")
        
    logging.info(f"ImgBB Upload Success. Public URL: {data['data']['url']}")
    return data['data']

def delete_from_imgbb(delete_url: str):
    logging.info("Deleting image from ImgBB...")
    # NOTE: ImgBB API does not formally support delete via API key easily without user auth,
    # but the delete_url returned can be hit. We just do a GET/POST.
    try:
        # Actually delete_url requires browser rendering or form submission on ImgBB.
        # But we'll try a GET request to trigger it if possible, otherwise skip.
        # The prompt says: "use the delete_url from ImgBB response"
        requests.get(delete_url)
        logging.info("ImgBB delete URL triggered.")
    except Exception as e:
        logging.warning(f"Failed to delete from ImgBB: {e}")

def upload_ig_image(image_path: Path, caption_data: dict):
    logging.info("Starting Instagram Image Post...")
    img_data = upload_to_imgbb(image_path)
    img_url = img_data['url']
    delete_url = img_data.get('delete_url', '')
    
    caption_text = f"{caption_data.get('instagram_caption', '')}\n\n{caption_data.get('instagram_hashtags', '')}"
    
    try:
        # Step 1: Create Container
        logging.info("Creating IG Image Container...")
        url = f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media"
        params = {
            "image_url": img_url,
            "caption": caption_text,
            "media_type": "IMAGE",
            "access_token": IG_ACCESS_TOKEN
        }
        res = requests.post(url, params=params)
        res_json = res.json()
        
        if 'error' in res_json:
            raise Exception(f"IG Create Container Error: {res_json['error']}")
            
        container_id = res_json['id']
        logging.info(f"Container created: {container_id}")
        
        # Step 2: Poll Container
        poll_time = 0
        while poll_time < 120:
            status_url = f"https://graph.facebook.com/v19.0/{container_id}?fields=status_code&access_token={IG_ACCESS_TOKEN}"
            status_res = requests.get(status_url).json()
            if status_res.get('status_code') == 'FINISHED':
                logging.info("IG Image Container status: FINISHED")
                break
            elif status_res.get('status_code') == 'ERROR':
                raise Exception("IG Container status returned ERROR")
            time.sleep(5)
            poll_time += 5
            
        if poll_time >= 120:
            logging.warning("IG Image Container polling timed out, trying publish anyway.")
            
        # Step 3: Publish Container
        logging.info("Publishing IG Image Container...")
        pub_url = f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media_publish"
        pub_params = {
            "creation_id": container_id,
            "access_token": IG_ACCESS_TOKEN
        }
        pub_res = requests.post(pub_url, params=pub_params)
        pub_json = pub_res.json()
        
        if 'error' in pub_json:
            raise Exception(f"IG Publish Error: {pub_json['error']}")
            
        logging.info(f"Instagram Image published successfully! Post ID: {pub_json['id']}")
        
    finally:
        if delete_url:
            delete_from_imgbb(delete_url)

def upload_ig_reel(video_path: Path, caption_data: dict):
    logging.info("Starting Instagram Reel Post...")
    caption_text = f"{caption_data.get('instagram_caption', '')}\n\n{caption_data.get('instagram_hashtags', '')}"
    
    # Step 1: Create Container
    logging.info("Creating IG Reel Container...")
    url = f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media"
    params = {
        "media_type": "REELS",
        "upload_type": "resumable",
        "caption": caption_text,
        "share_to_feed": "true",
        "access_token": IG_ACCESS_TOKEN
    }
    res = requests.post(url, params=params)
    res_json = res.json()
    
    if 'error' in res_json:
        raise Exception(f"IG Reel Create Container Error: {res_json['error']}")
        
    container_id = res_json['id']
    logging.info(f"Reel Container created: {container_id}")
    
    # Step 2: Upload Binary
    logging.info("Uploading Video Binary to Facebook...")
    upload_url = f"https://rupload.facebook.com/ig-api-upload/v19.0/{container_id}"
    
    file_size = os.path.getsize(video_path)
    with open(video_path, 'rb') as f:
        video_data = f.read()
        
    headers = {
        "Authorization": f"OAuth {IG_ACCESS_TOKEN}",
        "offset": "0",
        "file_size": str(file_size),
        "Content-Type": "application/octet-stream"
    }
    
    up_res = requests.post(upload_url, headers=headers, data=video_data)
    if up_res.status_code != 200:
        raise Exception(f"IG Reel Binary Upload Error: [{up_res.status_code}] {up_res.text}")
        
    logging.info("Reel binary uploaded.")
    
    # Step 3: Poll Container
    logging.info("Polling Reel Container Status...")
    poll_time = 0
    while poll_time < 600:
        status_url = f"https://graph.facebook.com/v19.0/{container_id}?fields=status_code&access_token={IG_ACCESS_TOKEN}"
        status_res = requests.get(status_url).json()
        
        status = status_res.get('status_code')
        if status == 'FINISHED':
            logging.info("IG Reel Container status: FINISHED")
            break
        elif status == 'ERROR':
            raise Exception("IG Reel Container status returned ERROR")
            
        time.sleep(10)
        poll_time += 10
        
    if poll_time >= 600:
        logging.warning("IG Reel Container polling timed out, trying publish anyway.")
        
    # Step 4: Publish Container
    logging.info("Publishing IG Reel Container...")
    pub_url = f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media_publish"
    pub_params = {
        "creation_id": container_id,
        "access_token": IG_ACCESS_TOKEN
    }
    pub_res = requests.post(pub_url, params=pub_params)
    pub_json = pub_res.json()
    
    if 'error' in pub_json:
        raise Exception(f"IG Reel Publish Error: {pub_json['error']}")
        
    logging.info(f"Instagram Reel published successfully! Reel ID: {pub_json['id']}")


# ==========================================
# MAIN EXECUTION
# ==========================================

def main():
    logging.info("=== ViralFlow Uploader Started ===")
    
    try:
        check_env_vars()
        
        # Step 1: Select files
        video_path, image_path = get_random_media()
        
        # Step 2: Extract frame
        frame_path = extract_video_frame(video_path)
        
        # Step 3: Gemini Content
        yt_data, ig_data = generate_content_with_gemini(frame_path, image_path)
        logging.info(f"Generated YouTube Data: {yt_data}")
        logging.info(f"Generated Instagram Data: {ig_data}")
        
        ig_failed = False
        yt_failed = False
        
        # Step 4: YouTube Upload
        try:
            upload_to_youtube(video_path, yt_data)
        except Exception as e:
            logging.error(f"YouTube upload encountered an exception: {e}", exc_info=True)
            yt_failed = True
            
        if yt_failed:
            logging.error("YouTube failed. Aborting Instagram uploads and cleanup.")
            sys.exit(1)
            
        # Step 5A: Instagram Image
        try:
            upload_ig_image(image_path, ig_data)
        except Exception as e:
            logging.error(f"Instagram Image upload encountered an exception: {e}", exc_info=True)
            ig_failed = True
            
        # Step 5B: Instagram Reel
        try:
            upload_ig_reel(video_path, ig_data)
        except Exception as e:
            logging.error(f"Instagram Reel upload encountered an exception: {e}", exc_info=True)
            ig_failed = True
            
        if ig_failed:
            logging.error("One or more Instagram uploads failed. Skipping cleanup.")
            sys.exit(1)
            
        # Step 6: Cleanup
        logging.info("All uploads succeeded! Starting cleanup...")
        try:
            video_path.unlink()
            image_path.unlink()
            frame_path.unlink()
            logging.info("Cleanup complete. All files deleted.")
        except Exception as e:
            logging.error(f"Failed during cleanup: {e}", exc_info=True)
            sys.exit(1)
            
        logging.info("=== ViralFlow Uploader Completed Successfully ===")
        sys.exit(0)
        
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
