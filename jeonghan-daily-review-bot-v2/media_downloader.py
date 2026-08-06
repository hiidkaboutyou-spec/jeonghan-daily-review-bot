import os
import subprocess
import urllib.request
import uuid

TEMP_DIR = "/tmp/jeonghan_media"

def ensure_temp_dir():
    if not os.path.exists(TEMP_DIR):
        os.makedirs(TEMP_DIR, exist_ok=True)

def download_video_yt_dlp(tweet_url: str) -> str:
    """
    Downloads high-quality video from Twitter/X using yt-dlp.
    Returns local file path of the downloaded mp4.
    """
    ensure_temp_dir()
    output_filename = os.path.join(TEMP_DIR, f"vid_{uuid.uuid4().hex[:8]}.mp4")
    
    # yt-dlp command to extract best quality mp4
    cmd = [
        "yt-dlp",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "-o", output_filename,
        "--no-playlist",
        tweet_url
    ]
    
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if os.path.exists(output_filename):
            return output_filename
    except Exception as e:
        print(f"yt-dlp download failed for {tweet_url}: {e}")
        
    return None

def download_image(image_url: str) -> str:
    """
    Downloads image from direct URL in high resolution.
    Returns local file path.
    """
    ensure_temp_dir()
    # Replace Twitter image size suffix with orig/large for highest quality
    if "format=" in image_url and "name=" in image_url:
        image_url = image_url.split("&name=")[0] + "&name=large"
    elif ":" in image_url and not image_url.endswith(".jpg") and not image_url.endswith(".png"):
        image_url = image_url.split(":")[0] + ":large"

    ext = ".jpg"
    if ".png" in image_url:
        ext = ".png"
        
    output_filename = os.path.join(TEMP_DIR, f"img_{uuid.uuid4().hex[:8]}{ext}")
    
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        req = urllib.request.Request(image_url, headers=headers)
        with urllib.request.urlopen(req) as response, open(output_filename, 'wb') as out_file:
            out_file.write(response.read())
        return output_filename
    except Exception as e:
        print(f"Image download failed for {image_url}: {e}")
        return None

if __name__ == "__main__":
    ensure_temp_dir()
    print("Media downloader initialized.")
