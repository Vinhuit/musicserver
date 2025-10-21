from flask import Flask, request, jsonify, Response, stream_with_context
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.formatters import TextFormatter
from googleapiclient.discovery import build
from ytmusicapi import YTMusic
import yt_dlp
import hashlib
import os
import json
import shutil
# ---------- CONFIG ----------
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "test_key")
CACHE_DIR = "music_cache"
os.makedirs(CACHE_DIR, exist_ok=True)

app = Flask(__name__)
youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
ytm = YTMusic()


# ---------- HELPERS ----------
def get_cache_id(query: str):
    return hashlib.md5(query.encode("utf-8")).hexdigest()

def make_meta_json(cache_id, artist, title, duration, thumbnail):
    """Build the JSON response"""
    return {
        "artist": artist,
        "audio_url": f"/{CACHE_DIR}/{cache_id}.mp3",
        "cover_url": thumbnail,
        "duration": duration,
        "from_cache": True,
        "lyric_url": f"/{CACHE_DIR}/{cache_id}.lrc",
        "title": title
    }


# ---------- STREAM PCM ----------
@app.route("/stream_pcm")
def stream_pcm():
    song = request.args.get("song")
    artist = request.args.get("artist", "")
    if not song:
        return jsonify({"error": "Missing song"}), 400

    query = f"{song} {artist}".strip()
    cache_id = get_cache_id(query)
    mp3_path = f"{CACHE_DIR}/{cache_id}.mp3"
    lrc_path = f"{CACHE_DIR}/{cache_id}.lrc"
    meta_path = f"{CACHE_DIR}/{cache_id}.json"

    # 1️⃣ If cached → return cached JSON immediately
    if os.path.exists(meta_path):
        with open(meta_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            data["from_cache"] = True
            return jsonify(data)

    # 2️⃣ Search YouTube
    search = youtube.search().list(
        q=query,
        part="id,snippet",
        maxResults=1,
        type="video"
    ).execute()

    if not search["items"]:
        return jsonify({"error": "No video found"}), 404

    video = search["items"][0]
    video_id = video["id"]["videoId"]
    title = video["snippet"]["title"]
    artist_name = video["snippet"]["channelTitle"]
    thumbnail = video["snippet"]["thumbnails"]["high"]["url"]

    # 3️⃣ Download audio
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": f"{CACHE_DIR}/{cache_id}.%(ext)s",
        "quiet": True,
        "noplaylist": True,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "128",
        }],
         "cookiefile": "/etc/secrets/cookiesyt.txt",  # Use cookies for authentication
    }
    cookie_file = "/etc/secrets/cookiesyt.txt"
    try:
        with open(cookie_file, "a") as f:
            pass  # Test if the file is writable
    except IOError:
        writable_cookie_file = "cookiesyt_writable.txt"
        shutil.copy(cookie_file, writable_cookie_file)
        cookie_file = writable_cookie_file

    # Update ydl_opts to use the writable cookie file
    ydl_opts["cookiefile"] = cookie_file

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
            duration = info.get("duration", 0)
    except Exception as e:
        return jsonify({"error": f"Download failed: {e}"}), 500

    # 4️⃣ Get lyrics (YouTube Music → fallback transcript)
    lyrics_text = ""
    try:
        search_ytm = ytm.search(query, filter="songs")
        if search_ytm:
            song_info = ytm.get_song(search_ytm[0]["videoId"])
            if "lyrics" in song_info and "browseId" in song_info["lyrics"]:
                lyrics_data = ytm.get_lyrics(song_info["lyrics"]["browseId"])
                if lyrics_data and lyrics_data.get("lyrics"):
                    lyrics_text = lyrics_data["lyrics"]
    except Exception:
        pass

    if not lyrics_text:
        try:
            transcript = YouTubeTranscriptApi().fetch(video_id, languages=["vi", "en"])
            formatter = TextFormatter()
            lyrics_text = formatter.format_transcript(transcript)
        except Exception:
            lyrics_text = ""

    # Save lyrics
    with open(lrc_path, "w", encoding="utf-8") as f:
        f.write(lyrics_text)

    # 5️⃣ Save metadata
    data = make_meta_json(cache_id, artist_name, title, duration, thumbnail)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return jsonify(data)


# ---------- STATIC FILE SERVE ----------
@app.route(f"/{CACHE_DIR}/<path:filename>")
def serve_cache(filename):
    full_path = os.path.join(CACHE_DIR, filename)
    if not os.path.exists(full_path):
        return jsonify({"error": "file not found"}), 404

    # Detect MIME
    if filename.endswith(".mp3"):
        mimetype = "audio/mpeg"
    elif filename.endswith(".lrc") or filename.endswith(".txt"):
        mimetype = "text/plain; charset=utf-8"
    elif filename.endswith(".json"):
        mimetype = "application/json"
    else:
        mimetype = "application/octet-stream"

    def generate():
        with open(full_path, "rb") as f:
            while chunk := f.read(4096):
                yield chunk
    return Response(stream_with_context(generate()), mimetype=mimetype)


# ---------- WEB UI ----------
@app.route("/")
def web_ui():
    return """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>🎵 ESP32 Music Server</title>
<style>
body {
  font-family: Arial, sans-serif;
  background: #101010;
  color: #eee;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-start;
  min-height: 100vh;
  margin: 0;
  padding: 2rem;
}
h2 {
  margin-bottom: 1rem;
  color: #00ff88;
}
input, button {
  font-size: 1rem;
  padding: 0.5rem;
  border-radius: 0.5rem;
  border: none;
  margin: 0.25rem;
}
input {
  width: 300px;
}
button {
  background: #00ff88;
  color: black;
  cursor: pointer;
}
button:hover {
  background: #00cc66;
}
img {
  border-radius: 0.75rem;
  margin: 1rem 0;
  box-shadow: 0 0 20px rgba(0,255,136,0.3);
}
#lyrics {
  white-space: pre-wrap;
  max-width: 600px;
  text-align: left;
  background: #181818;
  padding: 1rem;
  border-radius: 1rem;
  box-shadow: 0 0 10px rgba(255,255,255,0.1);
}
audio {
  margin-top: 1rem;
  width: 90%;
  max-width: 600px;
}
</style>
</head>
<body>
  <h2>🎶 ESP32 YouTube Music Server</h2>
  <div>
    <input id="song" type="text" placeholder="Enter song title..." />
    <input id="artist" type="text" placeholder="Artist (optional)" />
    <button onclick="searchMusic()">Search</button>
  </div>

  <div id="result" style="display:none;">
    <h3 id="title"></h3>
    <p id="artist_name"></p>
    <img id="cover" src="" width="200" />
    <audio id="player" controls></audio>
    <h4>Lyrics</h4>
    <div id="lyrics">Loading...</div>
  </div>

<script>
async function searchMusic() {
  const song = document.getElementById('song').value.trim();
  const artist = document.getElementById('artist').value.trim();
  if (!song) return alert("Please enter a song name!");
  const query = encodeURIComponent(song + " " + artist);
  document.getElementById('result').style.display = 'none';
  document.getElementById('lyrics').innerText = '';

  try {
    const res = await fetch(`/stream_pcm?song=${query}`);
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    document.getElementById('result').style.display = 'block';
    document.getElementById('title').innerText = data.title;
    document.getElementById('artist_name').innerText = data.artist;
    document.getElementById('cover').src = data.cover_url;
    document.getElementById('player').src = data.audio_url;

    const lrcRes = await fetch(data.lyric_url);
    const lrcText = await lrcRes.text();
    document.getElementById('lyrics').innerText = lrcText || 'No lyrics available.';
  } catch (err) {
    alert("Error: " + err.message);
  }
}
</script>
</body>
</html>
"""




# ---------- MAIN ----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, threaded=True, debug=True)




