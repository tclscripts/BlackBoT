import subprocess
import sys
import re

try:
    import yt_dlp
except ImportError:
    print("yt_dlp not found. Installing yt_dlp...")
    try:
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'yt_dlp'])
    except subprocess.CalledProcessError as e:
        print(f"Failed to install required package: {e}")
    import yt_dlp

try:
    import youtubesearchpython
except ImportError:
    print("youtube-search-python not found. Installing youtube-search-python...")
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'youtube-search-python'])
    import youtubesearchpython

from youtubesearchpython import VideosSearch


class YoutubeTitle:
    def search_youtube(query):
        videos_search = VideosSearch(query, limit=1)
        result = videos_search.result()
        if result['result']:
            video_id = result['result'][0]['id']
            return video_id
        return None

    def extract_youtube_id(text):
        # Regular expression to match YouTube video URLs
        regex = r'(https?://)?(www\.)?(youtube|youtu|youtube-nocookie)\.(com|be)/.+?(v=|\/)([a-zA-Z0-9_-]{11})'
        match = re.search(regex, text)
        if match:
            video_id = match.group(6)
            return video_id
        return None

    def get_video_info(video_id):
        url = f"https://www.youtube.com/watch?v={video_id}"
        # yt-dlp options
        ydl_opts = {
            'quiet': True,
            'skip_download': True,
            'force_generic_extractor': True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Extract video info
            info_dict = ydl.extract_info(url, download=False)
            # Get the title
            video_title = info_dict.get('title', 'N/A')
            video_views = info_dict.get('view_count', 'N/A')
            video_likes = info_dict.get('like_count', 'N/A')
            return video_title, video_views, video_likes
