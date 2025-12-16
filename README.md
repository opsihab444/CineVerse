# 🎬 CineVerse

A premium movie and TV series streaming platform built with FastAPI and modern web technologies.

![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python)
![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-green?logo=fastapi)
![License](https://img.shields.io/badge/License-MIT-yellow)

## ✨ Features

- 🎥 **Movie & TV Series Streaming** - Watch movies and TV shows with multiple quality options
- 🔍 **Smart Search** - Search for any content with instant suggestions
- 📱 **Responsive Design** - Beautiful UI that works on desktop and mobile
- ⚡ **Fast Performance** - Optimized with caching and async operations
- 🎨 **Premium UI** - Netflix-style design with smooth animations
- 🔄 **Keep-Alive System** - Built-in mechanism to prevent server sleeping on free tier hosting

## 🚀 Quick Start

### Prerequisites
- Python 3.11+
- pip

### Installation

```bash
# Clone the repository
git clone https://github.com/opsihab444/CineVerse.git
cd CineVerse

# Install dependencies
pip install -r requirements.txt

# Run the server
python main.py
```

Visit `http://localhost:8000` in your browser.

## 📁 Project Structure

```
CineVerse/
├── main.py              # FastAPI application
├── requirements.txt     # Python dependencies
├── render.yaml          # Render deployment config
├── static/
│   ├── favicon.svg      # App favicon
│   └── style.css        # Custom styles
├── templates/
│   ├── index.html       # Main page
│   ├── player.html      # Video player
│   └── search.html      # Search results
└── docs/
    └── API.md           # API documentation
```

## 🌐 API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | Homepage |
| `GET /health` | Health check (for monitoring) |
| `GET /api/home` | Get homepage content |
| `GET /api/details/{title}` | Get movie details |
| `GET /api/tv_details/{title}` | Get TV series details |
| `GET /api/search?q={query}` | Search content |
| `GET /api/stream_url/{title}` | Get movie stream URL |
| `GET /docs` | Swagger API documentation |

## 🚀 Deployment

### Deploy to Render

1. Push this repo to GitHub
2. Go to [render.com](https://render.com)
3. Create new **Web Service**
4. Connect your GitHub repo
5. Settings:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
6. Deploy!

The app has a built-in keep-alive mechanism that pings itself every 5 minutes to prevent Render's free tier from sleeping.

## 📄 License

MIT License - feel free to use this project for personal or commercial purposes.

## 👨‍💻 Author

Built with ❤️ by [opsihab444](https://github.com/opsihab444)
