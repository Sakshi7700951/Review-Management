# Backend – Python Flask API

## Setup

```bash
cd backend
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

The API runs on **http://localhost:2034** (same port as production, set via `PORT` env var or `.env`)

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check |
| GET | `/api/filters` | Returns unique emails & phones from CSV |
| GET | `/api/doctors?email=X&phone=Y&page=1&pageSize=50` | Filtered doctor list |
| GET | `/api/reviews?email=X&location=Y&page=1&pageSize=10` | All reviews (ReviewHub) |
| GET | `/api/critical-reviews?email=X&location=Y` | Critical reviews only (Dashboard, cached 4h) |
| GET | `/api/stats?email=X&location=Y` | Aggregate stats for stat cards |
| POST | `/api/reply` | Proxy reply to GMB |

### Reply Body
```json
{
  "email": "gmbaccess5@gmail.com",
  "text": "Thank you for your feedback!",
  "name": "accounts/.../reviews/..."
}
```

## CSV Database
`HarshDB_manipalfinaldatas.csv` must be in the same directory as `app.py`.

## Critical Review Refresh
Critical reviews (1–2 star) are cached in memory and auto-refreshed every **4 hours** per location using a background thread.
