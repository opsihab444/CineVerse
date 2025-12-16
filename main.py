import sys
import io
# Fix encoding for Windows console
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import uvicorn
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import httpx
import moviebox_api
from moviebox_api import Homepage, MovieDetails, MovieAuto, Session, resolve_media_file_to_be_downloaded, Search
from moviebox_api.download import DownloadableMovieFilesDetail, DownloadableTVSeriesFilesDetail
from moviebox_api.models import SearchResultsItem
import os
import asyncio

# --- CONFIGURATION ---
# Patch the host to match the working website
moviebox_api.constants.HOST_URL = "https://moviebox.ph/"

# --- MONKEY PATCH: Fix Pydantic Validation Errors for 'referer' ---
try:
    import moviebox_api.extractor.models.json as json_models
    from pydantic import HttpUrl
    from typing import Union, Optional

    # Relax referer fields to allow empty strings/invalid URLs
    # Patch MetadataModel
    json_models.MetadataModel.__annotations__['referer'] = Union[HttpUrl, str, None]
    json_models.MetadataModel.model_rebuild(force=True)

    # Patch PubParamModel 
    json_models.PubParamModel.__annotations__['referer'] = Union[HttpUrl, str, None]
    json_models.PubParamModel.model_rebuild(force=True)

    # Patch ResDataModel
    json_models.ResDataModel.__annotations__['referer'] = Union[HttpUrl, str, None]
    json_models.ResDataModel.model_rebuild(force=True)
    
    # Patch ItemJsonDetailsModel to reflect changes
    json_models.ItemJsonDetailsModel.model_rebuild(force=True)
    
    print("✅ Successfully patched Pydantic models for empty referer.")
except Exception as e:
    print(f"❌ Failed to patch models: {e}")
    import traceback
    traceback.print_exc()

# Catch ValidationError in the routes and fallback
from pydantic import ValidationError


import uuid
app = FastAPI(
    title="CineVerse API",
    description="Professional Movie & TV Streaming API with secure playback.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Resolve paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

# Mount static files
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Templates
# Templates
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# --- GLOBAL HTTP CLIENT ---
# Optimized for streaming high-throughput video
proxy_client = httpx.AsyncClient(
    timeout=httpx.Timeout(60.0, connect=10.0),
    limits=httpx.Limits(max_keepalive_connections=50, max_connections=100)
)

# --- KEEP ALIVE (Prevent Render Free Tier Sleep) ---
SELF_PING_INTERVAL = 300  # 5 minutes in seconds
keep_alive_task = None

async def keep_alive():
    """Background task to ping self and prevent Render from sleeping."""
    import os
    # Get the render URL from environment, fallback to localhost
    render_url = os.getenv("RENDER_EXTERNAL_URL", "http://localhost:8000")
    health_url = f"{render_url}/health"
    
    while True:
        try:
            await asyncio.sleep(SELF_PING_INTERVAL)
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(health_url)
                print(f"[KEEP-ALIVE] Pinged {health_url} - Status: {response.status_code}")
        except Exception as e:
            print(f"[KEEP-ALIVE] Ping failed: {e}")

@app.on_event("startup")
async def startup_event():
    """Start the keep-alive background task on server startup."""
    global keep_alive_task
    keep_alive_task = asyncio.create_task(keep_alive())
    print("[KEEP-ALIVE] Background ping task started!")

@app.on_event("shutdown")
async def shutdown_event():
    global keep_alive_task
    if keep_alive_task:
        keep_alive_task.cancel()
        print("[KEEP-ALIVE] Background ping task stopped!")
    await proxy_client.aclose()

# --- CACHING ---
# Simple in-memory cache for faster repeated requests
from functools import lru_cache
import time

# Cache for movie search results and details (expires after 10 minutes)
_movie_cache = {}
_cache_ttl = 600  # 10 minutes

def get_cached(key):
    """Get cached value if not expired."""
    if key in _movie_cache:
        value, timestamp = _movie_cache[key]
        if time.time() - timestamp < _cache_ttl:
            return value
        del _movie_cache[key]
    return None

def set_cached(key, value):
    """Cache a value with current timestamp."""
    _movie_cache[key] = (value, time.time())

# --- HELPERS ---

def get_session(client_ip=None):
    """
    Create a session.
    - For Localhost: Use natural connection (uses your PC's BD IP).
    - For Render/Remote: Forward the real client's IP.
    """
    is_localhost = not client_ip or client_ip in ["127.0.0.1", "localhost", "::1"]
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Origin': 'http://h5.aoneroom.com',
        'Referer': 'http://h5.aoneroom.com/',
        'CF-IPCountry': 'BD',
        'Accept-Language': 'bn-BD,bn;q=0.9,en-US;q=0.8,en;q=0.7',
        'X-Client-Country': 'BD',
        'X-Country-Code': 'BD',
        'X-Time-Zone': 'Asia/Dhaka',
        'X-Locale': 'bn_BD'
    }

    # Only forward IP if it's NOT localhost (Real User on Render)
    if not is_localhost:
        headers['X-Forwarded-For'] = client_ip
        headers['X-Real-IP'] = client_ip
    # else: Localhost -> No IP headers -> Uses your real PC IP (BD)

    # Check for actual Proxy URL (optional override)
    proxy_url = os.getenv("BD_PROXY_URL")
    if proxy_url:
        print(f"[SESSION] Using Real Proxy: {proxy_url}")
        return Session(proxy=proxy_url, headers=headers)
    
    return Session(headers=headers)

def get_image_url(item):
    # Try 'image', then 'cover', then 'img'
    img_obj = getattr(item, 'image', getattr(item, 'cover', getattr(item, 'img', None)))
    
    # If it's a model with 'url' attribute
    if hasattr(img_obj, 'url'):
        return str(img_obj.url)
    # If it's a dict and has 'url'
    if isinstance(img_obj, dict) and 'url' in img_obj:
        return str(img_obj['url'])
    # If it's already a string
    if isinstance(img_obj, str):
        return img_obj
    return ''

def get_title(item):
    return getattr(item, 'title', getattr(item, 'name', ''))

def get_id(item):
    # Try id, subjectId
    val = getattr(item, 'id', getattr(item, 'subjectId', ''))
    return str(val)

async def search_movie_by_title(title: str, session):
    """Search for a movie by title and return the first MOVIE result (not TV series)."""
    s = Search(session=session, query=title)
    results = await s.get_content_model()
    if results.items and len(results.items) > 0:
        # Try to find a movie (subjectType == 1 or MOVIES)
        for item in results.items:
            subject_type = getattr(item, 'subjectType', None)
            # SubjectType.MOVIES = 1
            if subject_type is not None:
                if hasattr(subject_type, 'value'):
                    if subject_type.value == 1:
                        return item
                elif subject_type == 1:
                    return item
        # Fallback: return first item anyway
        return results.items[0]
    return None

async def search_content_by_title(title: str, session):
    """Search for any content (movie or TV series) by title."""
    s = Search(session=session, query=title)
    results = await s.get_content_model()
    if results.items and len(results.items) > 0:
        return results.items[0]
    return None

def is_tv_series(item):
    """Check if an item is a TV series (subjectType == 2)."""
    subject_type = getattr(item, 'subjectType', None)
    if subject_type is not None:
        if hasattr(subject_type, 'value'):
            return subject_type.value == 2
        return subject_type == 2
    return False

# --- ROUTES ---

@app.get("/", response_class=HTMLResponse)
@app.get("/movies", response_class=HTMLResponse)
@app.get("/tv", response_class=HTMLResponse)
@app.get("/animation", response_class=HTMLResponse)
async def read_root(request: Request):
    """Render the homepage."""
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/player/{fid}", response_class=HTMLResponse)
async def player_page(request: Request, fid: str):
    """Render the player page."""
    return templates.TemplateResponse("player.html", {"request": request, "fid": fid})

# --- API ENDPOINTS ---

@app.get("/health", tags=["System"], summary="Health Check")
async def health_check():
    """Health check endpoint for keep-alive and monitoring."""
    return {"status": "ok", "message": "CineVerse is running! 🎬"}


def get_client_ip(request: Request):
    """Get the real client IP, handling proxies (Render/Cloudflare)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host

@app.get("/api/home", tags=["Discovery"], summary="Get Homepage Content")
async def get_home_content(request: Request, mode: str = "full"):
    """
    Fetch homepage content.
    Modes:
    - 'full': Get everything (default)
    - 'init': Get Banner + Top 2 Rows (Fast load)
    - 'more': Get the rest of the rows
    """
    client_ip = get_client_ip(request)
    # print(f"[HOME] Request from: {client_ip}")
    session = get_session(client_ip)
    try:
        # 1. Check Cache
        cache_key = "home_content_full"
        cached_data = get_cached(cache_key)
        
        sections = []
        
        if cached_data:
            print("[HOME] Cache Hit")
            sections = cached_data
        else:
            print("[HOME] Cache Miss - Fetching from Upstream...")
            hp = Homepage(session=session)
            content = await hp.get_content_model()
            
            # Process data for frontend
            
            # Add Banner (using 'contents' or 'banner')
            banner_items = []
            if hasattr(content, 'contents') and content.contents:
                 banner_items = content.contents
            elif hasattr(content, 'banner') and isinstance(content.banner, list):
                 banner_items = content.banner
            elif hasattr(content, 'bannerList') and content.bannerList:
                 banner_items = content.bannerList
    
            if banner_items:
                sections.append({
                    "title": "Featured",
                    "type": "banner",
                    "items": [
                        {
                            "title": get_title(item),
                            "id": get_id(item),
                            "image": get_image_url(item)
                        }
                        for item in banner_items
                    ]
                })
    
            # Add Operating List Sections
            if hasattr(content, 'operatingList'):
                for op in content.operatingList:
                    section_title = getattr(op, 'title', getattr(op, 'name', 'Unknown'))
                    
                    # Find sub-items
                    sub_items = []
                    if hasattr(op, 'items'): sub_items = op.items
                    elif hasattr(op, 'subjectList'): sub_items = op.subjectList
                    elif hasattr(op, 'bannerList'): sub_items = op.bannerList
                    elif hasattr(op, 'subjects'): sub_items = op.subjects
                    elif hasattr(op, 'banner'):
                        if isinstance(op.banner, list):
                            sub_items = op.banner
                        elif hasattr(op.banner, 'items'):
                            sub_items = op.banner.items
    
                    if sub_items:
                        clean_items = []
                        for item in sub_items:
                             img_url = get_image_url(item)
                             if img_url: 
                                 is_movie = True
                                 subject_type = getattr(item, 'subjectType', None)
                                 if subject_type is not None:
                                     if hasattr(subject_type, 'value'): is_movie = subject_type.value == 1
                                     else: is_movie = subject_type == 1
                                 if hasattr(item, 'subject') and item.subject:
                                     st = getattr(item.subject, 'subjectType', None)
                                     if st is not None:
                                         if hasattr(st, 'value'): is_movie = st.value == 1
                                         else: is_movie = st == 1
                                             
                                 clean_items.append({
                                    "title": get_title(item),
                                    "id": get_id(item),
                                    "image": img_url,
                                    "isMovie": is_movie,
                                    "genre": getattr(item, 'genre', [])
                                 })
                        
                        if clean_items:
                            sections.append({
                                "title": section_title,
                                "type": "row",
                                "items": clean_items
                            })
            
            # Cache the full processed sections list
            set_cached(cache_key, sections)
            print(f"[HOME] Cached {len(sections)} sections")

        # 2. Slice based on mode
        response_sections = []
        if mode == 'init':
            # Banner (if exists) + Top 2 rows
            # Usually Index 0 is Banner. 
            count = 0
            limit = 3 # Banner + 2 rows
            response_sections = sections[:limit]
        elif mode == 'more':
            # All excluding top 3
            response_sections = sections[3:] if len(sections) > 3 else []
        else:
            response_sections = sections

        return {"sections": response_sections}
        
    except Exception as e:
        print(f"Error fetching home: {e}")
        return {"error": str(e)}
    finally:
        if hasattr(session, 'aclose'): await session.aclose()




# --- HELPER: Professional Filename Generator ---
import re
def make_pro_filename(title, year=None, quality=None, is_tv=False, season=None, episode=None):
    # Simplified for internal metadata if needed, but not for URL
    return f"{title}.mp4"

def format_duration(seconds):
    if not seconds: return "N/A"
    try:
        seconds = int(seconds)
        h = seconds // 3600
        m = (seconds % 3600) // 60
        if h > 0:
            return f"{h}h {m}m"
        return f"{m}m"
    except:
        return "N/A"

def make_secure_url(token, title, quality="HD"):
    import time
    import random
    
    # Sanitize title for URL: Space -> Dot, remove special chars
    safe_title = str(title).strip()
    safe_title = re.sub(r'[^\w\s\-\.]', '', safe_title)
    safe_title = re.sub(r'\s+', '.', safe_title)
    
    # Generate OTT-style secure URL structure
    # Format: /v/{token}/{Title}.{Quality}.mp4?exp={timestamp}&sig={signature}
    exp = int(time.time()) + 21600 # 6 hours expiry
    sig = uuid.uuid4().hex[:8]
    
    # Clean quality string
    q_str = str(quality).replace(" ", "").replace(".", "")
    if not q_str: q_str = "HD"
    
    return f"/v/{token}/{safe_title}.{q_str}.mp4?token=exp={exp}&sig={sig}"
    
@app.get("/api/details/{title:path}", tags=["Movies"], summary="Get Movie Details")
async def get_details(request: Request, title: str, include_stream: bool = True):
    """
    Get detailed information about a movie by its title.
    
    - **title**: The title of the movie (or ID if supported).
    - **include_stream**: If true, attempts to resolve streaming URLs immediately.
    """
    
    # Check cache first
    cache_key = f"details:{title}:{include_stream}"
    cached = get_cached(cache_key)
    if cached:
        print(f"[DETAILS] Cache hit for: {title}")
        return cached
    
    client_ip = get_client_ip(request)
    session = get_session(client_ip)
    movie = None
    
    try:
        # Search for the movie first
        movie = await search_movie_by_title(title, session)
        if not movie:
            return {"error": f"Movie '{title}' not found"}
        
        # Define async functions for parallel fetching
        async def fetch_details():
            try:
                md = MovieDetails(movie, session=session)
                try:
                    json_details = await md.get_json_details_extractor_model()
                    
                    # Normal processing
                    subject = json_details.subject
                    directors = []
                    cast = []
                    if json_details.stars:
                        for s in json_details.stars:
                            if s.character and 'Director' in s.character:
                                directors.append(s.name)
                            elif hasattr(s, 'staffType') and s.staffType == 2:
                                directors.append(s.name)
                            else:
                                cast.append(s.name)

                    trailer_url = None
                    trailer_img = None
                    if hasattr(subject, 'trailer') and subject.trailer:
                        if subject.trailer.videoAddress:
                            trailer_url = str(subject.trailer.videoAddress.url)
                        if subject.trailer.cover:
                            trailer_img = str(subject.trailer.cover.url)

                    return {
                        "title": subject.title,
                        "description": subject.description,
                        "year": str(subject.releaseDate) if subject.releaseDate else 'N/A',
                        "rating": getattr(subject, 'imdbRatingValue', 'N/A'),
                        "image": get_image_url(subject),
                        "actors": cast,
                        "directors": directors,
                        "genre": subject.genre if subject.genre else [],
                        "duration": format_duration(getattr(subject, 'duration', 0)),
                        "country": getattr(subject, 'countryName', 'N/A'),
                        "trailerUrl": trailer_url,
                        "trailerImage": trailer_img,
                        "id": get_id(movie)
                    }
                except (ValidationError, Exception) as ve:
                    print(f"[DETAILS] Validation Error in MovieDetails: {ve}")
                    # Fallback to search result data
                    raise Exception("Validation failed, fallback to basics")

            except Exception as e:
                print(f"[DETAILS] MovieDetails failed, using search result: {e}")
                return {
                    "title": getattr(movie, 'title', title),
                    "description": getattr(movie, 'description', 'No description available.'),
                    "year": str(getattr(movie, 'releaseDate', 'N/A')),
                    "rating": getattr(movie, 'imdbRatingValue', 'N/A'),
                    "image": get_image_url(movie),
                    "actors": [],
                    "genre": getattr(movie, 'genre', []) or []
                }
        
        async def fetch_stream():
            if not include_stream:
                return None
            try:
                downloadable_details = DownloadableMovieFilesDetail(session, movie)
                download_metadata = await downloadable_details.get_content_model()
                if download_metadata:
                    # Extract qualities logic (reused)
                    qualities = []
                    if hasattr(download_metadata, 'downloads'):
                        for d in download_metadata.downloads:
                            res = getattr(d, 'resolution', None)
                            size = getattr(d, 'size', None)
                            d_url = getattr(d, 'url', None)
                            if d_url:
                                # Secure Token Generation
                                token = str(uuid.uuid4().hex)
                                _stream_map[token] = str(d_url)
                                
                                # OTT Style URL
                                q_label = f"{res}p" if res else "720p"
                                secure_url = make_secure_url(token, movie.title, q_label)
                                
                                qualities.append({
                                    "label": q_label,
                                    "url": secure_url,
                                    "size": size,
                                    "resolution": res
                                })
                    
                    # Sort qualities
                    def get_res_val(item):
                        try: return int(str(item['resolution']).replace('p', ''))
                        except: return 0
                    qualities.sort(key=get_res_val, reverse=True)

                    # Determine stream url
                    try:
                        media_file = resolve_media_file_to_be_downloaded("720P", download_metadata)
                    except:
                        try:
                            media_file = resolve_media_file_to_be_downloaded("BEST", download_metadata)
                        except:
                            # Fallback if resolve fails but we have downloads
                            if qualities:
                                # Return with tokenized quality URLs
                                return {
                                    "streamUrl": qualities[0]['url'], # This is now /stream/{token}
                                    "streamReferer": "https://fmoviesunblocked.net/",
                                    "qualities": qualities
                                }
                            return None

                    # If resolving was successful, we also need to mask this if no qualities list or just fallback
                    # Fallback stream generation
                    real_url = str(media_file.url)
                    token = str(uuid.uuid4().hex)
                    _stream_map[token] = real_url
                    
                    return {
                        "streamUrl": make_secure_url(token, movie.title, "Auto"),
                        "streamReferer": "https://fmoviesunblocked.net/",
                        "qualities": qualities
                    }
            except Exception as e:
                print(f"[DETAILS] Stream fetch error: {e}")
                return {"streamError": str(e)}
            return None
        
        # Fetch details and stream URL in PARALLEL for speed
        results = await asyncio.gather(fetch_details(), fetch_stream())
        
        data = results[0]  # Movie details
        stream_data = results[1]  # Stream URL
        
        if stream_data:
            data.update(stream_data)
            if "streamUrl" in stream_data:
                print(f"[DETAILS] Stream URL included")
        
        # Cache the result
        set_cached(cache_key, data)
        
        return data
            
    except Exception as e:
        print(f"[DETAILS] Error: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}
    finally:
        if hasattr(session, 'aclose'): await session.aclose()


@app.get("/api/tv_details/{title:path}", tags=["TV Series"], summary="Get TV Series Details")
async def get_tv_details(request: Request, title: str):
    """
    Get details for a TV series, including all seasons and episodes.
    """
    
    # Check cache first
    cache_key = f"tv_details_v2:{title}"
    cached = get_cached(cache_key)
    if cached:
        print(f"[TV] Cache hit for: {title}")
        return cached
    
    client_ip = get_client_ip(request)
    session = get_session(client_ip)
    
    try:
        # Search for the content - try different variations
        from moviebox_api.exceptions import ZeroSearchResultsError
        
        content = None
        search_queries = [
            title,
            title.replace("[Hindi]", "").replace("[Bengali]", "").strip(),
            title.split("[")[0].strip() if "[" in title else title
        ]
        
        for query in search_queries:
            try:
                content = await search_content_by_title(query, session)
                if content:
                    print(f"[TV] Found with query: {query}")
                    break
            except ZeroSearchResultsError:
                continue
            except Exception as e:
                print(f"[TV] Search error for '{query}': {e}")
                continue
        
        if not content:
            return {"error": f"Could not find '{title}'. Try searching with a simpler name."}
        
        # Check if it's actually a TV series
        is_series = is_tv_series(content)
        if not is_series:
            return {"error": "This item is a movie, not a TV series. Use the movie modal instead."}
        
        # Get details
        try:
            from moviebox_api import TVSeriesDetails
            td = TVSeriesDetails(content, session=session)
            details = await td.get_json_details_extractor_model()
            subject = details.subject
            
            # Extract Directors and Cast
            directors = []
            cast = []
            if details.stars:
                for s in details.stars:
                    if s.character == 'Director' or (hasattr(s, 'staffType') and s.staffType == 2):
                        directors.append(s.name)
                    else:
                        cast.append(s.name)
            
            # Extract Trailer
            trailer_url = None
            trailer_img = None
            if hasattr(subject, 'trailer') and subject.trailer:
                if subject.trailer.videoAddress:
                     trailer_url = str(subject.trailer.videoAddress.url)
                if subject.trailer.cover:
                     trailer_img = str(subject.trailer.cover.url)

            data = {
                "title": subject.title,
                "description": subject.description,
                "year": str(subject.releaseDate) if subject.releaseDate else 'N/A',
                "rating": getattr(subject, 'imdbRatingValue', 'N/A'),
                "image": get_image_url(subject),
                "actors": cast,
                "directors": directors,
                "genre": subject.genre if subject.genre else [],
                "duration": format_duration(getattr(subject, 'duration', 0)),
                "country": getattr(subject, 'countryName', 'N/A'),
                "trailerUrl": trailer_url,
                "trailerImage": trailer_img,
                "isTvSeries": True,
                "seasons": []
            }
            
            # Parse seasons from details.seasons
            # Each season has: se (season number), allEp (comma-separated episode numbers), maxEp
            try:
                if details.seasons:
                    for season in details.seasons:
                        # Ensure season_num is int
                        try:
                            season_num = int(getattr(season, 'se', 1))
                        except:
                            season_num = 1
                            
                        all_ep_str = getattr(season, 'allEp', '')
                        
                        # Parse episode numbers from comma-separated string
                        episodes = []
                        if all_ep_str:
                            ep_numbers = [int(x.strip()) for x in all_ep_str.split(',') if x.strip().isdigit()]
                            for ep_num in ep_numbers:
                                episodes.append({
                                    "episodeNumber": ep_num,
                                    "title": f"Episode {ep_num}",
                                    "image": data["image"],
                                    "seasonNum": season_num
                                })
                        
                        # Fallback: if no episodes found from allEp, use maxEp
                        if not episodes:
                            max_ep_raw = getattr(season, 'maxEp', 0)
                            try:
                                max_ep = int(max_ep_raw)
                            except (ValueError, TypeError):
                                max_ep = 0
                                
                            if max_ep > 0:
                                print(f"[TV] Season {season_num}: allEp missing, using maxEp={max_ep} to generate episodes")
                                for ep_num in range(1, max_ep + 1):
                                    episodes.append({
                                        "episodeNumber": ep_num,
                                        "title": f"Episode {ep_num}",
                                        "image": data["image"],
                                        "seasonNum": season_num
                                    })
                        
                        # Double check we have episodes
                        if not episodes:
                             print(f"[TV] Season {season_num}: No episodes found via allEp or maxEp.")

                        data["seasons"].append({
                            "seasonNumber": season_num,
                            "totalEpisodes": len(episodes),
                            "episodes": episodes
                        })
                    
                    print(f"[TV] Found {len(data['seasons'])} seasons")
                    for s in data["seasons"]:
                        print(f"  -> Season {s['seasonNumber']}: {s['totalEpisodes']} episodes")
                        
            except Exception as season_error:
                print(f"[TV] Error parsing seasons: {season_error}")
                import traceback
                traceback.print_exc()
            
            # If no seasons found, add a placeholder
            if not data["seasons"]:
                data["seasons"] = [{
                    "seasonNumber": 1,
                    "totalEpisodes": 1,
                    "episodes": [{"episodeNumber": 1, "title": "Episode 1", "image": data["image"], "seasonNum": 1}]
                }]
            
            # Cache the result
            set_cached(cache_key, data)
            return data
            
        except Exception as e:
            print(f"[TV] Error getting TV details: {e}")
            # Fallback to basic info
            return {
                "title": getattr(content, 'title', title),
                "description": getattr(content, 'description', 'No description available.'),
                "year": str(getattr(content, 'releaseDate', 'N/A')),
                "rating": getattr(content, 'imdbRatingValue', 'N/A'),
                "image": get_image_url(content),
                "isTvSeries": is_series,
                "seasons": [],
                "error": str(e)
            }
            
    except Exception as e:
        print(f"[TV] Error: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}
    finally:
        if hasattr(session, 'aclose'): await session.aclose()

@app.get("/api/stream_url/{title:path}", tags=["Movies"], summary="Get Movie Stream")
async def get_stream_url(request: Request, title: str, quality: str = "720P"):
    """
    Resolve a secure streaming URL for a specific movie.
    """
    
    # Force a high-quality BD IP for stream link generation to ensure speed/compatibility
    client_ip = "103.205.132.10"
    session = get_session(client_ip)
    try:
        print(f"[STREAM] Searching for stream: {title}")
        
        # 1. Search for the movie
        movie = await search_movie_by_title(title, session)
        if not movie:
            return {"error": f"Movie '{title}' not found"}
        
        print(f"[STREAM] Found: {movie.title}")
        
        # 2. Get downloadable files metadata
        downloadable_details = DownloadableMovieFilesDetail(session, movie)
        download_metadata = await downloadable_details.get_content_model()
        
        # Check if metadata is valid
        if download_metadata is None:
            return {"error": f"No downloadable files found for '{title}'"}
        
        print(f"[STREAM] Got download metadata")
        
        # Extract all available qualities
        available_qualities = []
        if hasattr(download_metadata, 'downloads'):
            for d in download_metadata.downloads:
                res = getattr(d, 'resolution', None)
                size = getattr(d, 'size', None)
                d_url = getattr(d, 'url', None)
                
                if d_url:
                    # Tokenize
                    token = str(uuid.uuid4().hex)
                    _stream_map[token] = str(d_url)
                    
                    # OTT Style URL
                    q_label = f"{res}p" if res else "720p"
                    secure_url = make_secure_url(token, movie.title, q_label)
                    
                    available_qualities.append({
                        "label": q_label,
                        "url": secure_url, # Key change
                        "size": size,
                        "resolution": res
                    })
        
        # Sort qualities descending by resolution (if numeric)
        def get_res_val(item):
            try: return int(str(item['resolution']).replace('p', ''))
            except: return 0
        available_qualities.sort(key=get_res_val, reverse=True)

        # 3. Resolve the media file URL for the requested quality (Default behavior)
        stream_url = ""
        filename = f"{movie.title}.mp4"
        
        if available_qualities:
             # Default to 720p or Best if requested not found
             selected = None
             
             # Try to find match
             for q_item in available_qualities:
                 if str(q_item['resolution']) in quality or q_item['label'] == quality:
                     selected = q_item
                     break
            
             # Fallback to first (Best/High Res due to sort)
             if not selected:
                 selected = available_qualities[0]
                 
             stream_url = selected['url']
        
        else:
            # Fallback to old method if 'downloads' list was empty for some reason
            try:
                media_file = resolve_media_file_to_be_downloaded(quality, download_metadata)
                real_url = str(media_file.url)
                token = str(uuid.uuid4().hex)
                _stream_map[token] = real_url
                stream_url = make_secure_url(token, movie.title, "Auto")
                
                filename = f"{movie.title}.mp4"
            except Exception as e:
                return {"error": f"No stream found: {e}"}

        return {
            "url": stream_url,
            "filename": filename,
            "title": movie.title,
            "referer": "https://fmoviesunblocked.net/",
            "qualities": available_qualities
        }
            
    except Exception as e:
        print(f"[STREAM] Error: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}
    finally:
        if hasattr(session, 'aclose'): await session.aclose()

@app.get("/api/tv_stream_url/{title:path}/{season}/{episode}", tags=["TV Series"], summary="Get TV Series Stream")
async def get_tv_stream_url(request: Request, title: str, season: int, episode: int, quality: str = "720P"):
    """
    Resolve a secure streaming URL for a specific TV episode.
    """
    # Force a high-quality BD IP for stream link generation to ensure speed/compatibility
    client_ip = "103.205.132.10"
    session = get_session(client_ip)
    try:
        print(f"[TV STREAM] Searching for: {title} S{season}E{episode}")
        
        # 1. Search (using moviebox search logic for generic content)
        s = Search(session=session, query=title)
        results = await s.get_content_model()
        if not results.items:
            # Try appending season info
            s = Search(session=session, query=f"{title} S{season}")
            results = await s.get_content_model()
            
        if not results.items:
            return {"error": f"Series '{title}' not found"}

        item = results.items[0]
        print(f"[TV STREAM] Found series: {item.title}")

        # 2. Get downloadable files metadata for TV Series
        downloadable_files = DownloadableTVSeriesFilesDetail(session, item)
        
        # 3. Get specific season/episode model
        try:
            downloadable_files_detail = await downloadable_files.get_content_model(
                season=season,
                episode=episode
            )
        except Exception as e:
            return {"error": f"Could not fetch episode details: {str(e)}"}
            
        if not downloadable_files_detail:
             return {"error": f"Episode S{season}E{episode} not found."}
             
        print(f"[TV STREAM] Got episode metadata")

        # Extract all available qualities
        available_qualities = []
        if hasattr(downloadable_files_detail, 'downloads'):
            for d in downloadable_files_detail.downloads:
                res = getattr(d, 'resolution', None)
                size = getattr(d, 'size', None)
                d_url = getattr(d, 'url', None)
                
                if d_url:
                    # Tokenize
                    token = str(uuid.uuid4().hex)
                    _stream_map[token] = str(d_url)
                    
                    # OTT Style
                    q_label = f"{res}p" if res else "720p"
                    secure_url = make_secure_url(token, f"{item.title}.S{season}E{episode}", q_label)
                    
                    available_qualities.append({
                        "label": q_label,
                        "url": secure_url,
                        "size": size,
                        "resolution": res
                    })
        
        # Sort qualities
        def get_res_val(item):
            try: return int(str(item['resolution']).replace('p', ''))
            except: return 0
        available_qualities.sort(key=get_res_val, reverse=True)

        # 4. Resolve URL
        stream_url = ""
        filename = f"{title}_S{season}E{episode}.mp4"
        
        if available_qualities:
             # Default to 720p or Best
             selected = None
             for q_item in available_qualities:
                 if str(q_item['resolution']) in quality or q_item['label'] == quality:
                     selected = q_item
                     break
             if not selected:
                 selected = available_qualities[0]
             stream_url = selected['url']

        else:
             # Fallback
             try:
                target_media_file = resolve_media_file_to_be_downloaded(quality, downloadable_files_detail)
                real_url = str(target_media_file.url)
                token = str(uuid.uuid4().hex)
                _stream_map[token] = real_url
                
                stream_url = make_secure_url(token, f"{item.title}.S{season}E{episode}", "Auto")
                filename = f"{item.title} S{season}E{episode}.mp4"
             except Exception as e:
                 return {"error": f"No playable sources: {e}"}

        return {
            "url": stream_url,
            "filename": filename,
            "title": f"{item.title} S{season}E{episode}",
            "referer": "https://fmoviesunblocked.net/",
            "qualities": available_qualities
        }

    except Exception as e:
        print(f"[TV STREAM] Error: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}
    finally:
        if hasattr(session, 'aclose'): 
            await session.aclose()


@app.get("/search", response_class=HTMLResponse, include_in_schema=False)
async def search_page(request: Request, q: str = ""):
    """Render the search results page."""
    return templates.TemplateResponse("search.html", {"request": request, "query": q})

@app.get("/api/search", tags=["Discovery"], summary="Search Content", description="Search for Movies and TV Series by keyword.")
async def api_search(request: Request, q: str):
    """
    Search for Movies and TV Series by keyword.
    """
    if not q:
        return {"results": []}
    
    client_ip = get_client_ip(request)
    session = get_session(client_ip)
    try:
        # Use the Search class from moviebox_api
        s = Search(session=session, query=q)
        results = await s.get_content_model()
        
        formatted_results = []
        if results.items:
            for item in results.items:
                # Determine type
                is_movie = True
                subject_type = getattr(item, 'subjectType', None)
                if subject_type is not None:
                    if hasattr(subject_type, 'value'):
                        is_movie = subject_type.value == 1
                    else:
                        is_movie = subject_type == 1
                
                # Get basic info
                formatted_results.append({
                    "title": get_title(item),
                    "id": get_id(item),
                    "image": get_image_url(item),
                    "isMovie": is_movie,
                    "year": str(getattr(item, 'releaseDate', '')),
                    "rating": getattr(item, 'imdbRatingValue', 'N/A')
                })
                
        return {"results": formatted_results}
        
    except Exception as e:
        print(f"[SEARCH] Error: {e}")
        return {"error": str(e), "results": []}
    finally:
        if hasattr(session, 'aclose'): await session.aclose()


# --- STREAM PROXY ---
# We need this to bypass Referer checks. The browser asks US for video, we ask the server with correct headers.

# --- STREAM PROXY ENGINE ---
# Memory store for stream mapping: token -> url
_stream_map = {}

async def stream_engine(url: str, request: Request):
    """Core logic to stream video from upstream."""
    if not url:
        raise HTTPException(status_code=400, detail="Missing URL")

    # Headers needed by the upstream server
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://fmoviesunblocked.net/",
        "Origin": "https://h5.aoneroom.com"
    }

    # Forward Range header
    range_header = request.headers.get("range")
    if range_header:
        headers["Range"] = range_header

    try:
        # Stream request
        req = proxy_client.build_request("GET", url, headers=headers)
        response = await proxy_client.send(req, stream=True, follow_redirects=True)
        
        # Build response headers
        response_headers = {
            "Accept-Ranges": "bytes",
            "Content-Type": response.headers.get("Content-Type", "video/mp4"),
        }
        if "Content-Length" in response.headers:
             response_headers["Content-Length"] = response.headers["Content-Length"]
        if "Content-Range" in response.headers:
             response_headers["Content-Range"] = response.headers["Content-Range"]
        
        async def stream_video():
            try:
                # Optimized chunk size: 512KB
                async for chunk in response.aiter_bytes(chunk_size=512 * 1024):
                    yield chunk
            except Exception as stream_err:
                print(f"[STREAM ERROR] {stream_err}")
            finally:
                await response.aclose()
        
        return StreamingResponse(
            stream_video(),
            status_code=response.status_code,
            headers=response_headers,
            media_type=response_headers["Content-Type"]
        )
            
    except Exception as e:
        print(f"[PROXY] Error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Proxy error: {str(e)}")

@app.get("/proxy_video", tags=["Streaming"], summary="Legacy Stream Proxy", description="Legacy endpoint for streaming video content. Use /v/{token}/{filename} for secure streaming.", include_in_schema=False)
async def proxy_video(url: str, request: Request):
    """Legacy Endpoint."""
    return await stream_engine(url, request)

@app.get("/v/{token}/{filename}", tags=["Streaming"], summary="Secure Stream Proxy", description="Secure Proxy Endpoint for streaming video content. Validates token signature and handles byte-range requests.")
async def stream_secure_ott(token: str, filename: str, request: Request):
    """
    Secure Proxy Endpoint for streaming video content.
    Validates token signature and handles byte-range requests.
    """
    real_url = _stream_map.get(token)
    if not real_url:
        raise HTTPException(status_code=404, detail="Secure link expired")
    return await stream_engine(real_url, request)

@app.get("/stream/{token}")
@app.get("/stream/{token}/{filename}")
async def stream_by_token(token: str, request: Request, filename: str = None):
    """Legacy Support."""
    real_url = _stream_map.get(token)
    if not real_url:
        raise HTTPException(status_code=404, detail="Stream link expired")
    return await stream_engine(real_url, request)


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
