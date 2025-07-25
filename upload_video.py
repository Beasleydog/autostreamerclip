#!/usr/bin/python
#!/usr/bin/env python3
"""
Auto-upload to YouTube ➜ poll processing ➜
publish *only* if no Content-ID claim was detected.

Required scopes:
  • https://www.googleapis.com/auth/youtube.upload   (unchanged)
  • https://www.googleapis.com/auth/youtube.readonly (videos.list polling)
"""
import http.client as httplib
import httplib2
import os, random, sys, time
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
import argparse

# ─── Retry / upload constants ──────────────────────────────────────────────────
httplib2.RETRIES = 1
MAX_RETRIES = 10
RETRIABLE_EXCEPTIONS = (
    httplib2.HttpLib2Error, IOError, httplib.NotConnected,
    httplib.IncompleteRead, httplib.ImproperConnectionState,
    httplib.CannotSendRequest, httplib.CannotSendHeader,
    httplib.ResponseNotReady, httplib.BadStatusLine,
)
RETRIABLE_STATUS_CODES = [500, 502, 503, 504]

# ─── OAuth / API constants ─────────────────────────────────────────────────────
CLIENT_SECRETS_FILE = "oauth/client_secret.json"
UPLOAD_SCOPE   = "https://www.googleapis.com/auth/youtube.upload"
MANAGE_SCOPE   = "https://www.googleapis.com/auth/youtube"        # ← NEW: for videos.update
READONLY_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
SCOPES = [UPLOAD_SCOPE, MANAGE_SCOPE, READONLY_SCOPE]              # ← use this
YOUTUBE_API_SERVICE_NAME = "youtube"
YOUTUBE_API_VERSION      = "v3"
VALID_PRIVACY_STATUSES = ("public", "private", "unlisted")

# ───────────────────────────────────────────────────────────────────────────────
def get_authenticated_service():
    """
    Re-auth if needed; cache user OAuth tokens in oauth/*.json
    """
    creds = None
    token_file = f"oauth/{os.path.basename(__file__)}-oauth2.json"
    scopes = SCOPES        # instead of [UPLOAD_SCOPE, READONLY_SCOPE]

    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, scopes)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, scopes)
            creds = flow.run_local_server(port=0)
        
        with open(token_file, "w") as token:
            token.write(creds.to_json())

    service = build(YOUTUBE_API_SERVICE_NAME, YOUTUBE_API_VERSION, credentials=creds)
    return service


def initialize_upload(youtube, options):
    """
    Uploads the file **unlisted**, returns the new video_id.
    """
    tags = options.keywords.split(",") if options.keywords else None

    body = dict(
        snippet=dict(
            title       = options.title,
            description = options.description,
            tags        = tags,
            categoryId  = options.category,
        ),
        status=dict(
            privacyStatus="unlisted",          ### CHANGED: always unlisted first
            selfDeclaredMadeForKids=False,
        ),
    )

    insert_request = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=MediaFileUpload(options.file, chunksize=-1, resumable=True),
    )
    return resumable_upload(insert_request)      ### CHANGED: now returns video_id


def resumable_upload(insert_request):
    """
    Standard exponential-backoff upload. Returns the video_id when done.
    """
    response, error, retry = None, None, 0
    
    while response is None:
        try:
            status, response = insert_request.next_chunk()
            
            if response is not None and "id" in response:
                vid = response["id"]
                return vid
            else:
                sys.exit(f"Unexpected upload response: {response}")
                
        except HttpError as e:
            if e.resp.status in RETRIABLE_STATUS_CODES:
                error = f"A retriable HTTP error {e.resp.status}: {e.content}"
            else:
                raise
        except RETRIABLE_EXCEPTIONS as e:
            error = f"A retriable error occurred: {e}"

        if error:
            retry += 1
            if retry > MAX_RETRIES:
                sys.exit("Giving up.")
            sleep = random.random() * (2 ** retry)
            time.sleep(sleep)
            error = None  # Reset error for next iteration


# ─── NEW: poll & publish logic ─────────────────────────────────────────────────
def set_thumbnail(youtube, video_id, thumbnail_path):
    """
    Set the thumbnail for a YouTube video.
    
    Args:
        youtube: Authenticated YouTube service
        video_id: YouTube video ID
        thumbnail_path: Path to the thumbnail image file
        
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        print(f"🖼️  Setting thumbnail for video {video_id}")
        
        youtube.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(thumbnail_path)
        ).execute()
        
        print(f"✅ Thumbnail set successfully")
        return True
        
    except Exception as e:
        print(f"❌ Failed to set thumbnail: {e}")
        return False


def poll_and_publish(youtube, video_id, poll_interval=30, timeout=1800):
    """
    Polls videos.list(status,processingDetails,contentDetails) until
    processingStatus == 'succeeded'.  If Content-ID claim detected
    (contentDetails.licensedContent == True) keep video unlisted and log.
    Else patch privacyStatus -> public.
    """
    start = time.time()

    while True:
        elapsed = time.time() - start
        
        resp = youtube.videos().list(
            part="status,processingDetails,contentDetails",
            id=video_id,
        ).execute()

        if not resp.get("items"):
            sys.exit("Video not found")
            
        item = resp["items"][0]
        
        # Check processing status
        processing_details = item.get("processingDetails", {})
        proc_status = processing_details.get("processingStatus", "unknown")
        
        if proc_status == "succeeded":
            break
        elif proc_status == "failed":
            sys.exit("Processing failed.")
            
        if elapsed > timeout:
            sys.exit("Timed out waiting for processing.")
            
        time.sleep(poll_interval)

    # Check for Content-ID claims
    content_details = item.get("contentDetails", {})
    claimed = content_details.get("licensedContent", False)
    
    if claimed:
        print(f"Content-ID claim detected on video {video_id} - keeping unlisted")
    else:
        try:
            youtube.videos().update(
                part="status",
                body={"id": video_id, "status": {"privacyStatus": "public"}},
            ).execute()
            print(f"Video {video_id} published as public: https://www.youtube.com/watch?v={video_id}")
        except Exception as e:
            print(f"Failed to update privacy status: {e}")


# ─── CLI boilerplate ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True, help="Video file to upload")
    parser.add_argument("--title", default="Test Title", help="Video title")
    parser.add_argument("--description", default="Test Description")
    parser.add_argument("--category", default="22", help="Numeric video category")
    parser.add_argument("--keywords", default="", help="Comma-separated keywords")
    args = parser.parse_args()

    if not os.path.exists(args.file):
        sys.exit("Invalid --file path")
    
    yt = get_authenticated_service()

    try:
        vid = initialize_upload(yt, args)
        poll_and_publish(yt, vid)
        
    except HttpError as e:
        print(f"HTTP error occurred: {e.resp.status} - {e.content}")