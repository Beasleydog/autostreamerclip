import os
import time
import io
from typing import Optional, Union
import requests
from google import genai
from google.genai import types
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# ---------------------------------------------------------------------------
# Re-use a single Client instance so we do not create new gRPC pools for every
# clip.  This keeps memory stable across long sessions.
# ---------------------------------------------------------------------------

_CLIENT: Optional[genai.Client] = None


def _get_client(api_key: str) -> genai.Client:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = genai.Client(api_key=api_key)
    return _CLIENT

#NOTE THAT WE SHOULD ALWAYS BE USING GEMINI-2.5-FLASH, THIS IS NOT A TYPO.

def ask_gemini(prompt: str, api_key: Optional[str] = None, model: str = "gemini-2.5-flash", max_retries: int = 3) -> str:
    """
    Ask Gemini a text-only question using the official Google Gemini package.
    
    Args:
        prompt: The text prompt to send to Gemini
        api_key: Optional API key (defaults to GEMINI_API_KEY environment variable)
        model: The Gemini model to use (default: gemini-2.5-flash)
        max_retries: Maximum number of retries if response is empty (default: 3)
        
    Returns:
        Gemini's response as a string
    """
    api_key = api_key or os.getenv('GEMINI_API_KEY')
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable not set. Please create a .env file with GEMINI_API_KEY=your_api_key_here")
    
    client = _get_client(api_key)
    
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt
            )
            
            # Check if response is empty after trimming
            response_text = response.text.strip() if response.text else ""
            if response_text:
                return response_text
            else:
                print(f"Attempt {attempt + 1}/{max_retries}: Received empty response, retrying...")
                if attempt < max_retries - 1:
                    time.sleep(2)  # Wait 2 seconds before retrying
                    
        except Exception as e:
            if attempt == max_retries - 1:  # Last attempt
                raise Exception(f"Gemini API request failed: {str(e)}")
            print(f"Attempt {attempt + 1}/{max_retries} failed: {str(e)}, retrying...")
            time.sleep(2)
    
    # If we get here, all attempts returned empty responses
    print(f"Warning: All {max_retries} attempts returned empty responses")
    return ""


def wait_for_file_activation(client, file_name: str, max_wait_time: int = 300) -> bool:
    """
    Wait for a file to become ACTIVE before using it.
    
    Args:
        client: Gemini client instance
        file_name: Name of the uploaded file
        max_wait_time: Maximum time to wait in seconds (default: 5 minutes)
        
    Returns:
        True if file becomes active, False if timeout or failed
    """
    start_time = time.time()
    while time.time() - start_time < max_wait_time:
        try:
            file_info = client.files.get(name=file_name)
            if file_info.state == "ACTIVE":
                return True
            elif file_info.state == "FAILED":
                print(f"File {file_name} failed to activate, deleting it...")
                try:
                    client.files.delete(name=file_name)
                    print(f"Successfully deleted failed file: {file_name}")
                except Exception as delete_error:
                    print(f"Warning: Failed to delete failed file {file_name}: {delete_error}")
                return False
            print(f"Waiting for file {file_name} to activate... Current state: {file_info.state}")
            time.sleep(5)  # Wait 5 seconds before checking again
        except Exception as e:
            print(f"Error checking file state: {e}")
            time.sleep(5)
    
    return False


def ask_gemini_with_video(video_path: str, prompt: str, api_key: Optional[str] = None, max_upload_retries: int = 3, max_content_retries: int = 3, model: str = "gemini-2.5-flash") -> str:
    api_key = api_key or os.getenv('GEMINI_API_KEY')
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable not set. Please create a .env file with GEMINI_API_KEY=your_api_key_here")
    
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")
    
    client = _get_client(api_key)
    uploaded_file = None
    
    def generate_content_with_retry(response_func):
        """Helper function to retry content generation if response is empty"""
        for content_attempt in range(max_content_retries):
            try:
                response = response_func()
                response_text = response.text.strip() if response.text else ""
                if response_text:
                    return response_text
                else:
                    print(f"Content attempt {content_attempt + 1}/{max_content_retries}: Received empty response, retrying...")
                    if content_attempt < max_content_retries - 1:
                        time.sleep(2)
            except Exception as e:
                if content_attempt == max_content_retries - 1:
                    raise e
                print(f"Content attempt {content_attempt + 1}/{max_content_retries} failed: {str(e)}, retrying...")
                time.sleep(2)
        
        print(f"Warning: All {max_content_retries} content generation attempts returned empty responses")
        return ""
    
    try:
        # Check file size to determine upload method
        file_size = os.path.getsize(video_path)
        print(f"File size: {file_size / (1024*1024):.2f} MB")
        
        # Use resumable upload with manual chunked streaming
        print("Using resumable file upload (chunked streaming)…")

        for attempt in range(max_upload_retries):
            try:
                print(f"Upload attempt {attempt + 1}/{max_upload_retries}")

                file_name = upload_file_resumable(video_path, api_key)

                # Wait for file to become ACTIVE before we can use it
                print("Waiting for file to activate…")
                if not wait_for_file_activation(client, file_name):
                    raise RuntimeError("File failed to activate after upload")

                uploaded_file = client.files.get(name=file_name)

                def file_api_response():
                    return client.models.generate_content(
                        model=model,
                        contents=[uploaded_file, prompt]
                    )

                return generate_content_with_retry(file_api_response)

            except Exception as upload_error:
                print(f"Upload attempt {attempt + 1} failed: {upload_error}")

                # Best-effort cleanup (no need to fail if already deleted)
                try:
                    if uploaded_file:
                        client.files.delete(name=uploaded_file.name)
                except Exception:
                    pass

                uploaded_file = None

                if attempt < max_upload_retries - 1:
                    print("Retrying upload in 5 seconds…")
                    time.sleep(5)
                else:
                    raise Exception(f"Failed to upload and activate file after {max_upload_retries} attempts: {upload_error}")
        
        # This should never be reached, but just in case
        return ""
        
    except Exception as e:
        raise Exception(f"Failed to analyze video {video_path}: {str(e)}")
    finally:
        # Clean up uploaded file if it exists
        if uploaded_file:
            try:
                print(f"Cleaning up file: {uploaded_file.name}")
                client.files.delete(name=uploaded_file.name)
                print("File cleanup successful")
            except Exception as cleanup_error:
                print(f"Warning: Failed to cleanup file {uploaded_file.name}: {cleanup_error}")


def upload_file_resumable(file_path: str, api_key: str, chunk_size: int = 5*8 * 1024 * 1024) -> str:
    """Upload *file_path* to Gemini using a resumable, chunked upload.

    This avoids loading the full video into RAM by streaming the file in
    ``chunk_size`` byte pieces. Returns the server-generated file *name*
    (e.g. ``files/abcd1234``) which can be passed to other File API calls.
    """

    SESSION_URL = "https://generativelanguage.googleapis.com/upload/v1beta/files"

    total_size = os.path.getsize(file_path)

    print(f"[UPLOAD] Starting resumable upload → {os.path.basename(file_path)}  "
          f"{total_size / (1024 * 1024):.2f} MB in chunks of {chunk_size / (1024 * 1024):.2f} MB")

    # 1. Initiate a resumable upload session
    init_headers = {
        "X-Goog-Upload-Protocol": "resumable",
        "X-Goog-Upload-Command": "start",
        "X-Goog-Upload-Header-Content-Type": "video/mp4",
        "X-Goog-Upload-Header-Content-Length": str(total_size),
        "Content-Type": "application/json",
    }

    init_payload = {
        "file": {
            "display_name": os.path.basename(file_path)
        }
    }

    init_resp = requests.post(
        f"{SESSION_URL}?uploadType=resumable&key={api_key}",
        headers=init_headers,
        json=init_payload,
        timeout=120,
    )

    if init_resp.status_code not in {200, 201}:
        raise RuntimeError(f"Could not initiate upload session: {init_resp.text}")

    upload_url = init_resp.headers.get("X-Goog-Upload-URL") or init_resp.headers.get("x-goog-upload-url")
    if not upload_url:
        raise RuntimeError("Upload URL not returned by Gemini API")

    print(f"[UPLOAD] Resumable session URL received")

    offset = 0

    with open(file_path, "rb") as f:
        while True:
            buf = f.read(chunk_size)
            if not buf:
                break

            headers = {
                "Content-Type": "video/mp4",
                "Content-Length": str(len(buf)),
                "X-Goog-Upload-Offset": str(offset),
                "X-Goog-Upload-Command": "upload, finalize" if offset + len(buf) == total_size else "upload",
                "X-Goog-Upload-Protocol": "resumable",
            }

            put_resp = requests.put(upload_url, headers=headers, data=io.BytesIO(buf), timeout=300)

            if put_resp.status_code not in {200, 201, 308}:
                raise RuntimeError(
                    f"Chunk upload failed at offset {offset}: {put_resp.status_code} – {put_resp.text}"
                )

            offset += len(buf)

            status_type = "FINAL" if headers["X-Goog-Upload-Command"].startswith("upload, finalize") else "CHUNK"
            print(f"[UPLOAD] {status_type} OK – bytes {offset-len(buf)}-{offset-1}  "
                  f"({offset / total_size * 100:.1f}% done)  → HTTP {put_resp.status_code}")

    print("[UPLOAD] All chunks uploaded – waiting for server to finalise file resource …")

    # --- Parse the final response ----------------------------
    # The Gemini Files API may respond with either of these shapes:
    # 1. {"file": {"name": "files/abc-123", ...}}
    # 2. {"name": "files/abc-123", ...}
    # Accept both to stay forward-compatible.

    try:
        file_info_raw = put_resp.json()
    except ValueError:
        # Service returned non-JSON – surface the raw text to help debugging.
        raise RuntimeError(
            f"Upload finished but response was not valid JSON: {put_resp.text}"
        )

    file_resource = file_info_raw["file"] if "file" in file_info_raw else file_info_raw

    name = file_resource.get("name")
    if not name:
        raise RuntimeError(
            f"Upload finished but no file name in response: {file_info_raw}"
        )

    print(f"[UPLOAD] Upload complete – file name: {name}")
    return name

