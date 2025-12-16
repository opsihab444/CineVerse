# CineVerse API Reference

Welcome to the CineVerse API documentation. This API allows you to access movie and TV show data, search for content, and resolve streaming URLs.

**Base URL**: `http://localhost:8000` (Local Development)

## Authentication
Currently, the API is public and does not require authentication tokens for read-only access.

---

## Endpoints

### 1. Discovery

#### Get Home Content
Fetch the latest featured, trending, and popular content for the dashboard.

- **URL**: `/api/home`
- **Method**: `GET`
- **Response Example**:
```json
{
  "sections": [
    {
      "title": "Featured",
      "type": "banner",
      "items": [
        {
          "title": "Dune: Part Two",
          "id": "12345",
          "image": "https://example.com/dune.jpg"
        }
      ]
    },
    {
      "title": "Trending Movies",
      "type": "row",
      "items": [...]
    }
  ]
}
```

#### Search
Search for movies and TV series.

- **URL**: `/api/search`
- **Method**: `GET`
- **Query Parameters**:
    - `q` (string, required): The search keyword.
- **Response Example**:
```json
{
  "results": [
    {
      "title": "Inception",
      "id": "tt1375666",
      "image": "https://example.com/inception.jpg",
      "isMovie": true,
      "year": "2010",
      "rating": "8.8"
    }
  ]
}
```

---

### 2. Movies

#### Get Movie Details
Get detailed information about a specific movie.

- **URL**: `/api/details/{title}`
- **Method**: `GET`
- **Path Parameters**:
    - `title`: The title of the movie (e.g., `Inception`).
- **Query Parameters**:
    - `include_stream` (boolean, default: `true`): If `true`, includes streaming URLs in the response.
- **Response Example**:
```json
{
  "title": "Inception",
  "description": "A thief who steals corporate secrets...",
  "year": "2010",
  "rating": "8.8",
  "image": "...",
  "actors": ["Leonardo DiCaprio"],
  "streamUrl": "/v/token/Inception.720p.mp4",
  "qualities": [
    { "label": "1080p", "url": "..." },
    { "label": "720p", "url": "..." }
  ]
}
```

#### Get Movie Stream
Resolve a direct streaming URL for a movie.

- **URL**: `/api/stream_url/{title}`
- **Method**: `GET`
- **Query Parameters**:
    - `quality`: Preferred quality (e.g., `1080P`, `720P`).
- **Response Example**:
```json
{
  "url": "/v/token/movie.mp4",
  "filename": "Inception.mp4",
  "qualities": [...]
}
```

---

### 3. TV Series

#### Get TV Details
Get details, seasons, and episodes for a TV series.

- **URL**: `/api/tv_details/{title}`
- **Method**: `GET`
- **Response Example**:
```json
{
  "title": "Breaking Bad",
  "seasons": [
    {
      "seasonNumber": 1,
      "episodes": [
        {
          "episodeNumber": 1,
          "title": "Pilot",
          "image": "..."
        }
      ]
    }
  ]
}
```

#### Get TV Episode Stream
Resolve a streaming URL for a specific episode.

- **URL**: `/api/tv_stream_url/{title}/{season}/{episode}`
- **Method**: `GET`
- **Response Example**:
```json
{
  "url": "/v/token/episode.mp4",
  "filename": "Breaking Bad S01E01.mp4"
}
```

---

## Streaming Architecture
The API uses a secure proxy system to bypass upstream Referer checks.
- Streaming URLs follow the format: `/v/{token}/{filename}`.
- These URLs are valid for a limited time (6 hours).
- They support standard HTTP Range headers for seeking.
