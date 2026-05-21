"""
Review Management Backend v5
- MongoDB Atlas as primary data source (no CSV fallback for dashboard)
- Real 24h / 4h stats from DB
- Engagement volume: 6 buckets of 4h each (last 24h)
- Branch heatmap: rating distribution counts (≥4.5, ≥4.0, ≥3.5, ≥3.0, <3.0)
- Critical reviews: strictly last 24h from DB
- GMB API every 4h → saves to MongoDB
"""

import os, time, threading, math, datetime, requests


def _load_dotenv_paths():
    """Load simple KEY=VALUE pairs from project .env files into os.environ.
    This is a minimal loader (no quoting/expansion) to help local dev on Windows.
    """
    here = os.path.dirname(__file__)
    candidates = [os.path.join(here, ".env"), os.path.join(here, "..", ".env")]
    for p in candidates:
        try:
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as fh:
                    for ln in fh:
                        ln = ln.strip()
                        if not ln or ln.startswith("#") or "=" not in ln:
                            continue
                        k, v = ln.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip('"').strip("'")
                        if k and k not in os.environ:
                            os.environ[k] = v
        except Exception:
            pass


# attempt to load .env early for local development
_load_dotenv_paths()
from flask import Flask, jsonify, request
from flask_cors import CORS
import pandas as pd

app = Flask(__name__)
CORS(app)

# ── Config ─────────────────────────────────────────────────────────────────────
CSV_PATH        = os.path.join(os.path.dirname(__file__), "HarshDB_manipalfinaldatas.csv")
GMB_API_URL     = "https://multipliersolutions.in/gmbhospitals/gmb_api/api.php"
REFRESH_4H      = 4 * 60 * 60
OLLAMA_URL      = os.environ.get("OLLAMA_URL",   "https://ollama.com/api/chat")
OLLAMA_API_KEY  = os.environ.get("OLLAMA_API_KEY", "")
OLLAMA_COOKIE   = os.environ.get("OLLAMA_COOKIE", "aid=ba14a101-09b7-4628-8369-ce2808cbc8b7")
OLLAMA_MODEL    = os.environ.get("OLLAMA_MODEL",  "deepseek-v3.1:671b-cloud")
# Prefer environment configuration; if not provided we'll auto-detect the correct
# database and collections at runtime so nothing is hard-coded.
MONGO_URI       = os.environ.get("MONGO_URI")
MONGO_DB_NAME   = os.environ.get("MONGO_DB_NAME")
MONGO_REVIEWS_COLLECTION = os.environ.get("MONGO_REVIEWS_COLLECTION")
MONGO_DOCTORS_COLLECTION = os.environ.get("MONGO_DOCTORS_COLLECTION")
MONGO_PERSIST_REVIEWS = os.environ.get("MONGO_PERSIST_REVIEWS", "1").strip().lower() not in ("0","false","no","off")

# ── MongoDB ────────────────────────────────────────────────────────────────────
_db = None
_db_lock = threading.Lock()

def get_reviews_collection(db):
    global MONGO_REVIEWS_COLLECTION
    try:
        cols = db.list_collection_names()
    except Exception:
        cols = []
    if MONGO_REVIEWS_COLLECTION and MONGO_REVIEWS_COLLECTION in cols:
        return db[MONGO_REVIEWS_COLLECTION]
    # prefer obvious names
    for c in cols:
        if "review" in c.lower():
            MONGO_REVIEWS_COLLECTION = c
            return db[c]
    # fallback to first collection
    if cols:
        MONGO_REVIEWS_COLLECTION = cols[0]
        return db[cols[0]]
    raise RuntimeError("No collections found in detected MongoDB database")


def _detect_db_and_collections(client):
    """Try to find the database and sensible collection names automatically.
    Returns (db_name, reviews_collection, doctors_collection) or (None,None,None).
    """
    try:
        dbs = [d for d in client.list_database_names() if d not in ("admin","local","config")]
    except Exception:
        dbs = []
    preferred_reviews = "manipalfinalreviews"
    preferred_doctors = "manipalfinaldatas"
    for dbname in dbs:
        try:
            cols = client[dbname].list_collection_names()
            if preferred_reviews in cols:
                return dbname, preferred_reviews, (preferred_doctors if preferred_doctors in cols else None)
        except Exception:
            continue
    # try looser matching
    for dbname in dbs:
        try:
            cols = client[dbname].list_collection_names()
            for c in cols:
                name = c.lower()
                if "manipal" in name and "review" in name:
                    # try to find a doctors collection too
                    doccol = next((x for x in cols if "manipal" in x.lower() and "data" in x.lower()), None)
                    return dbname, c, doccol
        except Exception:
            continue
    # final fallback: pick first non-system DB with any collections
    for dbname in dbs:
        try:
            cols = client[dbname].list_collection_names()
            if cols:
                rev = next((c for c in cols if "review" in c.lower()), cols[0])
                doc = next((c for c in cols if "data" in c.lower()), None)
                return dbname, rev, doc
        except Exception:
            continue
    return None, None, None


class DatabaseWrapper:
    def __init__(self, db):
        self._db = db

    def __getattr__(self, name):
        return getattr(self._db, name)

    @property
    def reviews(self):
        return self._db[MONGO_REVIEWS_COLLECTION]

    @property
    def doctors(self):
        global MONGO_DOCTORS_COLLECTION
        if not MONGO_DOCTORS_COLLECTION:
            try:
                cols = self._db.list_collection_names()
                candidates = [c for c in cols if any(k in c.lower() for k in ("doctor","data","profile","details"))]
                if candidates:
                    MONGO_DOCTORS_COLLECTION = candidates[0]
                elif cols:
                    MONGO_DOCTORS_COLLECTION = cols[0]
            except Exception:
                pass
        if not MONGO_DOCTORS_COLLECTION:
            raise RuntimeError("Could not determine MongoDB doctors collection")
        return self._db[MONGO_DOCTORS_COLLECTION]


def format_iso_datetime(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


CRITICAL_STAR_RATINGS = ["ONE","TWO","1",1,"2",2]
POSITIVE_STAR_RATINGS = ["FOUR","FIVE","4",4,"5",5]
NEUTRAL_STAR_RATINGS   = ["THREE","3",3]


def resolved_numeric_rating_expr():
    return {
        "$cond": [
            {"$gt":["$_numericRating",0]},
            "$_numericRating",
            {"$switch": {
                "branches": [
                    {"case":{"$in":["$starRating", CRITICAL_STAR_RATINGS]}, "then":1},
                    {"case":{"$in":["$starRating", NEUTRAL_STAR_RATINGS]},  "then":3},
                    {"case":{"$in":["$starRating", POSITIVE_STAR_RATINGS]}, "then":4},
                    {"case":{"$eq":["$starRating", None]}, "then":0},
                    {"case":{"$eq":["$starRating", ""]}, "then":0}
                ],
                "default": 0
            }}
        ]
    }


def query_critical_ratings():
    return {"$or":[
        {"_numericRating":{"$lte":2,"$gt":0}},
        {"starRating":{"$in": CRITICAL_STAR_RATINGS}}
    ]}


def query_positive_ratings():
    return {"$or":[
        {"_numericRating":{"$gte":4}},
        {"starRating":{"$in": POSITIVE_STAR_RATINGS}}
    ]}


def normalize_review_doc(r):
    if not isinstance(r, dict):
        return r
    if r.get("_doctorName") is None:
        candidate = r.get("business_name") or r.get("doctorName") or r.get("clinicName") or ""
        if not candidate:
            name_field = str(r.get("name","")).strip()
            if not name_field.startswith("accounts/") and not name_field.startswith("locations/") and not name_field.startswith("reviews/"):
                candidate = name_field
        r["_doctorName"] = candidate
    if r.get("_doctorBranch") is None:
        r["_doctorBranch"] = r.get("Branch") or r.get("branch") or ""
    if r.get("_doctorCluster") is None:
        r["_doctorCluster"] = r.get("Cluster") or r.get("cluster") or ""
    if r.get("_doctorEmail") is None:
        r["_doctorEmail"] = r.get("mail_id") or r.get("email") or ""
    if r.get("_numericRating") is None:
        star = r.get("starRating")
        if isinstance(star, (int, float)):
            r["_numericRating"] = star
        elif isinstance(star, str):
            sr = star.strip().upper()
            if sr in ("ONE", "1"): r["_numericRating"] = 1
            elif sr in ("TWO", "2"): r["_numericRating"] = 2
            elif sr in ("THREE", "3"): r["_numericRating"] = 3
            elif sr in ("FOUR", "4"): r["_numericRating"] = 4
            elif sr in ("FIVE", "5"): r["_numericRating"] = 5
            else:
                try:
                    r["_numericRating"] = float(star)
                except Exception:
                    r["_numericRating"] = 0
        else:
            r["_numericRating"] = 0
    return r   

def sanitize_doctor_name(name):
    name = str(name or "").strip()
    if name.startswith("accounts/") or name.startswith("locations/") or name.startswith("reviews/"):
        return ""
    return name


def parse_review_create_time(create_time):
    if not create_time:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(create_time).replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        return None


def create_time_query(start, end=None):
    expr_value = {
        "$cond":[
            {"$eq":[{"$type":"$createTime"},"string"]},
            {"$dateFromString":{"dateString":"$createTime"}},
            "$createTime"
        ]
    }
    conditions = [{"$gte":[expr_value, start]}]
    if end is not None:
        conditions.append({"$lt":[expr_value, end]})
    return {"$expr":{"$and":conditions}}

def create_time_before(cutoff):
    expr_value = {
        "$cond":[
            {"$eq":[{"$type":"$createTime"},"string"]},
            {"$dateFromString":{"dateString":"$createTime"}},
            "$createTime"
        ]
    }
    return {"$expr":{"$lt":[expr_value, cutoff]}}


def _duplicate_ids_by_name(db):
    pipeline = [
        {"$match": {"name": {"$exists": True, "$ne": ""}}},
        {"$sort": {"name": 1, "createTime": -1, "_savedAt": -1}},
        {"$group": {"_id": "$name", "ids": {"$push": "$_id"}, "count": {"$sum": 1}}},
        {"$match": {"count": {"$gt": 1}}}
    ]
    ids = []
    for group in db.reviews.aggregate(pipeline):
        ids.extend(group["ids"][1:])
    return ids


def _duplicate_ids_by_signature(db):
    pipeline = [
        {"$match": {"$or": [{"name": {"$exists": False}}, {"name": ""}]}},
        {"$project": {
            "key": {
                "$concat": [
                    {"$ifNull": ["$business_name", ""]}, "|",
                    {"$ifNull": ["$comment", ""]}, "|",
                    {"$ifNull": ["$createTime", ""]}, "|",
                    {"$ifNull": ["$starRating", ""]}, "|",
                    {"$ifNull": ["$reviewer.displayName", ""]}, "|",
                    {"$ifNull": ["$mail_id", ""]}
                ]
            },
            "_id": 1
        }},
        {"$sort": {"key": 1}},
        {"$group": {"_id": "$key", "ids": {"$push": "$_id"}, "count": {"$sum": 1}}},
        {"$match": {"count": {"$gt": 1}}}
    ]
    ids = []
    for group in db.reviews.aggregate(pipeline):
        ids.extend(group["ids"][1:])
    return ids


def get_db():
    global _db
    if _db is not None:
        return _db
    with _db_lock:
        if _db is not None:
            return _db
        try:
            from pymongo import MongoClient
            if not MONGO_URI:
                app.logger.warning("MONGO_URI not configured in environment")
                return None
            client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
            client.admin.command("ping")
            # Auto-detect DB/collections if environment variables weren't provided
            global MONGO_DB_NAME, MONGO_REVIEWS_COLLECTION, MONGO_DOCTORS_COLLECTION
            if not MONGO_DB_NAME:
                detected_db, detected_reviews, detected_doctors = _detect_db_and_collections(client)
                if detected_db:
                    MONGO_DB_NAME = detected_db
                    if detected_reviews:
                        MONGO_REVIEWS_COLLECTION = detected_reviews
                    if detected_doctors:
                        MONGO_DOCTORS_COLLECTION = detected_doctors
                    app.logger.info(f"Auto-detected MongoDB: {MONGO_DB_NAME} / reviews:{MONGO_REVIEWS_COLLECTION} / doctors:{MONGO_DOCTORS_COLLECTION}")
                else:
                    app.logger.warning("Could not auto-detect MongoDB database and collections")
                    return None

            raw_db = client[MONGO_DB_NAME]
            _db = DatabaseWrapper(raw_db)
            # Indexes for fast queries
            col = get_reviews_collection(raw_db)
            col.create_index([("createTime", -1)])
            col.create_index([("_location", 1)])
            col.create_index([("_numericRating", 1)])
            col.create_index([("_savedAt", -1)])
            col.create_index([("Branch", 1)])
            col.create_index([("Cluster", 1)])
            col.create_index([("_doctorBranch", 1)])
            col.create_index([("_doctorCluster", 1)])
            col.create_index([("business_name", 1)])
            app.logger.info("✅ MongoDB Atlas connected")
            return _db
        except Exception as e:
            app.logger.warning(f"MongoDB unavailable: {e}")
            return None

def save_reviews_to_db(location, doctor_name, branch, cluster, email, reviews):
    if not MONGO_PERSIST_REVIEWS:
        app.logger.info("Skipping review persistence because MONGO_PERSIST_REVIEWS is disabled")
        return
    db = get_db()
    if db is None:
        return
    try:
        doctor_name = sanitize_doctor_name(doctor_name)
        star_map = {"ONE":1,"TWO":2,"THREE":3,"FOUR":4,"FIVE":5}
        now = datetime.datetime.utcnow()
        ops = []
        from pymongo import ReplaceOne
        for r in reviews:
            raw_rating = r.get("starRating","")
            n = star_map.get(str(raw_rating).upper(), 0)
            if n == 0:
                try: n = int(raw_rating)
                except: n = 0
            doc = {
                **r,
                "_location":      location,
                "_doctorName":    doctor_name,
                "_doctorBranch":  branch,
                "_doctorCluster": cluster,
                "_doctorEmail":   email,
                "_numericRating": n,
                "_savedAt":       now,
            }
            if r.get("name"):
                ops.append(ReplaceOne({"name": r["name"]}, doc, upsert=True))
        if ops:
            db.reviews.bulk_write(ops, ordered=False)
    except Exception as e:
        app.logger.warning(f"DB save error: {e}")

def save_reply_to_db(review_name, reply_text, email):
    db = get_db()
    if db is None:
        return
    try:
        db.replies.insert_one({
            "review_name": review_name,
            "reply_text":  reply_text,
            "email":       email,
            "replied_at":  datetime.datetime.utcnow(),
        })
        db.reviews.update_one({"name": review_name}, {"$set": {"reviewReply": {"comment": reply_text}}})
    except Exception as e:
        app.logger.warning(f"DB reply save error: {e}")

# ── In-memory cache (fallback) ─────────────────────────────────────────────────
_mem_cache = {}
_mem_lock  = threading.Lock()

def mem_get(location):
    with _mem_lock:
        return _mem_cache.get(location)

def mem_set(location, all_reviews, critical_reviews):
    with _mem_lock:
        _mem_cache[location] = {
            "all_reviews":      all_reviews,
            "critical_reviews": critical_reviews,
            "fetched_at":       time.time(),
        }

def is_stale(location):
    entry = mem_get(location)
    if not entry:
        return True
    return (time.time() - entry["fetched_at"]) > REFRESH_4H

# ── CSV (for doctor list only) ──────────────────────────────────────────────────
_CSV_DF = None
_CSV_AT = 0

def get_csv_df(force=False):
    global _CSV_DF, _CSV_AT
    now = time.time()
    if _CSV_DF is not None and not force and (now - _CSV_AT) < REFRESH_4H:
        return _CSV_DF
    # Prefer MongoDB doctors collection when available (production/dev with MONGO_URI)
    db = get_db()
    if db is not None:
        try:
            docs = list(db.doctors.find({}, {"_id": 0}))
            if docs:
                df = pd.DataFrame(docs)
                df = df.where(pd.notnull(df), None)
                app.logger.info(f"Loaded doctors from MongoDB: {len(df)} rows")
                _CSV_DF = df
                _CSV_AT = now
                return _CSV_DF
        except Exception as e:
            app.logger.warning(f"Failed to load doctors from MongoDB: {e}")

    # If DB not available or empty, fall back to CSV file
    try:
        df = pd.read_csv(CSV_PATH, low_memory=False)
        if "_id" in df.columns:
            df = df.drop_duplicates(subset=["_id"], keep="first")
        df = df.where(pd.notnull(df), None)
        app.logger.info(f"CSV loaded: {len(df)} rows")
    except FileNotFoundError:
        app.logger.warning(f"CSV not found at {CSV_PATH}; doctors list will be empty if DB unavailable")
        df = pd.DataFrame()

    _CSV_DF = df
    _CSV_AT = now
    return _CSV_DF

def safe_int(v):
    try:
        f = float(v)
        return 0 if (math.isnan(f) or math.isinf(f)) else int(f)
    except: return 0

def load_doctors():
    df = get_csv_df()
    keep = ["_id","business_name","name","phone","account","mail_id","Cluster",
            "Branch","averageRating","totalReviewCount","address","primaryCategory",
            "profile_screenshot","placeId","mapsUri","newReviewUri"]
    existing = [c for c in keep if c in df.columns]
    df = df[existing].copy().where(pd.notnull(df[existing]), None)
    return df.to_dict(orient="records")

# ── GMB API ────────────────────────────────────────────────────────────────────
def fetch_gmb_reviews(email, location):
    all_reviews, page_token = [], ""
    while True:
        for attempt in range(3):
            try:
                resp = requests.post(
                    GMB_API_URL,
                    json={"function":"reviews","email":email,"location":location,"pageToken":page_token},
                    headers={"Content-Type":"application/json"},
                    timeout=30,
                )
                if resp.status_code == 500:
                    return all_reviews
                resp.raise_for_status()
                data = resp.json()
                if data.get("error"):
                    return all_reviews
                all_reviews.extend(data.get("reviews", []))
                page_token = data.get("nextPageToken","")
                break
            except requests.exceptions.Timeout:
                if attempt == 2: return all_reviews
                time.sleep(2)
            except Exception as e:
                app.logger.error(f"GMB error: {e}")
                if attempt == 2: return all_reviews
                time.sleep(1)
        if not page_token:
            break
    return all_reviews

def classify_critical(reviews):
    star_map = {"ONE":1,"TWO":2,"THREE":3,"FOUR":4,"FIVE":5}
    out = []
    for r in reviews:
        raw = r.get("starRating","")
        n = star_map.get(str(raw).upper(), 0)
        if n == 0:
            try: n = int(raw)
            except: n = 0
        if 0 < n <= 2:
            r["_numericRating"] = n
            r["_isCritical"] = True
            out.append(r)
    return out

# ── Live stats ─────────────────────────────────────────────────────────────────
_live_stats = {
    "isLive":False,"totalDoctors":0,"totalReviews":0,"criticalCount":0,
    "positiveCount":0,"neutralCount":0,"averageRating":0,"positivePct":0,
    "neutralPct":0,"criticalPct":0,"lastFetchedAt":0,
    "stats4h":{"totalReviews":0,"criticalCount":0},
    "stats24h":{"totalReviews":0,"criticalCount":0}
}
_live_stats_lock = threading.Lock()

def _build_live_stats():
    global _live_stats
    app.logger.info("Building live stats from GMB...")
    df = get_csv_df()
    valid = df[df["mail_id"].notna() & df["account"].notna()]

    now_ts    = datetime.datetime.utcnow()
    cutoff4h  = now_ts - datetime.timedelta(hours=4)
    cutoff24h = now_ts - datetime.timedelta(hours=24)

    star_map = {"ONE":1,"TWO":2,"THREE":3,"FOUR":4,"FIVE":5}
    total_reviews=0; critical=0; positive=0; neutral=0; all_ratings=[]
    rev4h=0; crit4h=0; rev24h=0; crit24h=0

    for _, row in valid.iterrows():
        email    = str(row["mail_id"]).strip()
        location = str(row["account"]).strip()
        branch   = str(row.get("Branch") or "")
        cluster  = str(row.get("Cluster") or "")
        dname    = sanitize_doctor_name(str(row.get("name") or row.get("business_name") or ""))
        if not dname:
            dname = str(row.get("business_name") or "")
        try:
            reviews = fetch_gmb_reviews(email, location)
            crit    = classify_critical(reviews)
            mem_set(location, reviews, crit)
            save_reviews_to_db(location, dname, branch, cluster, email, reviews)

            total_reviews += len(reviews)
            critical      += len(crit)

            for r in reviews:
                raw = r.get("starRating","")
                n = star_map.get(str(raw).upper(),0)
                if n==0:
                    try: n=int(raw)
                    except: n=0
                if n>0:
                    all_ratings.append(n)
                    if n>=4: positive+=1
                    elif n==3: neutral+=1

                dt = parse_review_create_time(r.get("createTime"))
                if dt:
                    if dt >= cutoff4h:
                        rev4h+=1
                        if n<=2 and n>0: crit4h+=1
                    if dt >= cutoff24h:
                        rev24h+=1
                        if n<=2 and n>0: crit24h+=1
            time.sleep(0.05)
        except Exception as e:
            app.logger.warning(f"GMB failed {email}: {e}")

    avg = round(sum(all_ratings)/len(all_ratings),2) if all_ratings else 0
    with _live_stats_lock:
        _live_stats = {
            "isLive":True, "totalDoctors":len(valid),
            "totalReviews":total_reviews, "criticalCount":critical,
            "positiveCount":positive, "neutralCount":neutral,
            "averageRating":avg,
            "positivePct":round(positive/total_reviews*100,1) if total_reviews else 0,
            "neutralPct": round(neutral/total_reviews*100,1)  if total_reviews else 0,
            "criticalPct":round(critical/total_reviews*100,1) if total_reviews else 0,
            "lastFetchedAt":time.time(),
            "stats4h": {"totalReviews":rev4h,  "criticalCount":crit4h},
            "stats24h":{"totalReviews":rev24h, "criticalCount":crit24h},
        }
    app.logger.info(f"Live stats done: {total_reviews} reviews, {critical} critical")

def _build_live_stats_from_db():
    """Build live stats purely from MongoDB — fast path used at startup."""
    db = get_db()
    if db is None:
        return False
    try:
        now_ts    = datetime.datetime.utcnow()
        cutoff4h  = now_ts - datetime.timedelta(hours=4)
        cutoff24h = now_ts - datetime.timedelta(hours=24)

        # Total counts
        total_reviews = db.reviews.count_documents({})
        if total_reviews == 0:
            return False

        pipeline_all = [
            {"$addFields":{"_resolvedRating": resolved_numeric_rating_expr()}},
            {"$group":{
                "_id": None,
                "avgRating":    {"$avg": "$_resolvedRating"},
                "positiveCount":{"$sum":{"$cond":[{"$gte":["$_resolvedRating",4]},1,0]}},
                "neutralCount": {"$sum":{"$cond":[{"$and":[{"$gte":["$_resolvedRating",3]},{"$lt":["$_resolvedRating",4]}]},1,0]}},
                "criticalCount":{"$sum":{"$cond":[{"$and":[{"$gt":["$_resolvedRating",0]},{"$lte":["$_resolvedRating",2]}]},1,0]}},
            }}
        ]
        agg = list(db.reviews.aggregate(pipeline_all))
        if not agg:
            return False
        a = agg[0]

        # 24h stats
        rev24h  = db.reviews.count_documents(create_time_query(cutoff24h))
        crit24h = db.reviews.count_documents({
            "$and":[create_time_query(cutoff24h), query_critical_ratings()]
        })

        # 4h stats
        rev4h  = db.reviews.count_documents(create_time_query(cutoff4h))
        crit4h = db.reviews.count_documents({
            "$and":[create_time_query(cutoff4h), query_critical_ratings()]
        })

        # Doctor count from CSV
        try:
            df    = get_csv_df()
            ndocs = len(df[df["mail_id"].notna() & df["account"].notna()])
        except:
            ndocs = 0

        pos  = a.get("positiveCount",0) or 0
        neu  = a.get("neutralCount",0)  or 0
        crit = a.get("criticalCount",0) or 0
        avg  = round(a.get("avgRating",0) or 0, 2)

        with _live_stats_lock:
            global _live_stats
            _live_stats = {
                "isLive":True, "totalDoctors":ndocs,
                "totalReviews":total_reviews,
                "criticalCount":crit, "positiveCount":pos, "neutralCount":neu,
                "averageRating":avg,
                "positivePct":round(pos/total_reviews*100,1) if total_reviews else 0,
                "neutralPct": round(neu/total_reviews*100,1) if total_reviews else 0,
                "criticalPct":round(crit/total_reviews*100,1) if total_reviews else 0,
                "lastFetchedAt":time.time(),
                "stats4h": {"totalReviews":rev4h,  "criticalCount":crit4h},
                "stats24h":{"totalReviews":rev24h, "criticalCount":crit24h},
            }
        app.logger.info(f"✅ DB stats: {total_reviews} total, {rev24h} in 24h, {crit24h} critical 24h")
        return True
    except Exception as e:
        app.logger.error(f"_build_live_stats_from_db error: {e}")
        return False

def _auto_refresh_loop():
    while True:
        time.sleep(REFRESH_4H)
        try:
            app.logger.info("4h auto-refresh triggered")
            get_csv_df(force=True)
            _build_live_stats()
        except Exception as e:
            app.logger.error(f"Auto-refresh error: {e}")

def _preload():
    time.sleep(0.5)
    try:
        get_csv_df()
        # Try fast DB stats first, then full GMB fetch
        if not _build_live_stats_from_db():
            app.logger.info("No DB data yet — starting GMB fetch...")
            _build_live_stats()
        else:
            app.logger.info("Stats loaded from MongoDB Atlas ✅")
    except Exception as e:
        app.logger.error(f"Preload error: {e}")

# ── Ollama ─────────────────────────────────────────────────────────────────────
def call_ollama(system_prompt, user_msg, max_tokens=400):
    if not OLLAMA_API_KEY:
        return None
    try:
        resp = requests.post(
            OLLAMA_URL,
            headers={"Content-Type":"application/json",
                     "Authorization":f"Bearer {OLLAMA_API_KEY}",
                     "Cookie":OLLAMA_COOKIE},
            json={"model":OLLAMA_MODEL,"stream":False,
                  "messages":[{"role":"system","content":system_prompt},
                               {"role":"user","content":user_msg}]},
            timeout=45,
        )
        data = resp.json()
        return ((data.get("message") or {}).get("content") or
                ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
    except Exception as e:
        app.logger.error(f"Ollama error: {e}")
        return None

# ═══════════════════════════════════════════════════════════════════════════════
# API ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/health")
def health():
    db = get_db()
    return jsonify({"status":"ok","db_connected": db is not None})


@app.route("/api/debug-db")
def debug_db():
    """Temporary debug endpoint: returns DB connection, counts, and a sample document."""
    db = get_db()
    now = datetime.datetime.utcnow()
    cut24h = now - datetime.timedelta(hours=24)
    result = {"db_connected": db is not None}
    if db is None:
        return jsonify(result)
    try:
        total = db.reviews.count_documents({})
        last24 = db.reviews.count_documents(create_time_query(cut24h))
        crit24 = db.reviews.count_documents({"$and":[create_time_query(cut24h), query_critical_ratings()]})
        sample = db.reviews.find_one({}, {"_id":0})
        result.update({"total_reviews": total, "reviews_24h": last24, "critical_24h": crit24, "sample": sample})
    except Exception as e:
        result["error"] = str(e)
    return jsonify(result)

@app.route("/api/cleanup-reviews", methods=["POST"])
def cleanup_reviews():
    """Remove duplicate and stale review documents from MongoDB."""
    db = get_db()
    if db is None:
        return jsonify({"error":"MongoDB not connected"}), 500

    days = request.args.get("days", "30").strip()
    dry_run = request.args.get("dryRun", "0").strip().lower() in ("1","true","yes","on")
    try:
        age_days = int(days)
    except ValueError:
        return jsonify({"error":"Invalid days value"}), 400

    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=age_days)
    removed = {"duplicatesByName": 0, "duplicatesBySignature": 0, "old": 0}

    try:
        # Remove duplicate reviews by review name.
        duplicate_ids = _duplicate_ids_by_name(db)
        if duplicate_ids:
            removed["duplicatesByName"] = len(duplicate_ids)
            if not dry_run:
                db.reviews.delete_many({"_id": {"$in": duplicate_ids}})

        # Remove duplicate reviews by generated signature for docs without a stable name.
        signature_ids = _duplicate_ids_by_signature(db)
        if signature_ids:
            removed["duplicatesBySignature"] = len(signature_ids)
            if not dry_run:
                db.reviews.delete_many({"_id": {"$in": signature_ids}})

        # Remove old review docs by createTime or saved timestamp.
        old_query = {"$or": [create_time_before(cutoff), {"_savedAt": {"$lt": cutoff}}]}
        if dry_run:
            removed["old"] = db.reviews.count_documents(old_query)
        else:
            result = db.reviews.delete_many(old_query)
            removed["old"] = result.deleted_count

        return jsonify({"success": True, "dryRun": dry_run, "ageDays": age_days, "removed": removed})
    except Exception as e:
        app.logger.error(f"cleanup-reviews error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/reset-reviews", methods=["POST"])
def reset_reviews():
    """Permanently delete all review documents in the reviews collection."""
    db = get_db()
    if db is None:
        return jsonify({"error":"MongoDB not connected"}), 500

    confirm = request.args.get("confirm", "").strip()
    if confirm != "RESET":
        return jsonify({"error":"confirm=RESET is required to delete all reviews"}), 400

    dry_run = request.args.get("dryRun", "0").strip().lower() in ("1","true","yes","on")
    if dry_run:
        count = db.reviews.count_documents({})
        return jsonify({"success": True, "dryRun": True, "count": count, "message": "No documents deleted"})

    result = db.reviews.delete_many({})
    return jsonify({"success": True, "deletedCount": result.deleted_count})


@app.route("/api/last-refresh")
def last_refresh():
    with _live_stats_lock:
        fetched = _live_stats.get("lastFetchedAt",0)
    return jsonify({
        "lastFetchedAt": fetched,
        "nextRefreshIn": max(0, REFRESH_4H-(time.time()-fetched)),
        "refreshIntervalSeconds": REFRESH_4H,
        "isLive": _live_stats.get("isLive",False),
    })

# ── 1. Global stats ────────────────────────────────────────────────────────────
@app.route("/api/global-stats")
def global_stats():
    with _live_stats_lock:
        s = dict(_live_stats)
    if not s["isLive"]:
        # Try to build from DB on the fly
        _build_live_stats_from_db()
        with _live_stats_lock:
            s = dict(_live_stats)
    return jsonify(s)

# ── 2. Force refresh ───────────────────────────────────────────────────────────
@app.route("/api/force-refresh", methods=["POST"])
def force_refresh():
    try:
        get_csv_df(force=True)
        threading.Thread(target=_build_live_stats, daemon=True).start()
        return jsonify({"success":True,"message":"GMB refresh started — check /api/global-stats in ~2-3 min"})
    except Exception as e:
        return jsonify({"success":False,"error":str(e)}), 500

# ── 3. Engagement volume — 6 buckets of 4h each (last 24h) ────────────────────
@app.route("/api/engagement-volume")
def engagement_volume():
    """Returns review counts in 6 x 4-hour buckets for the last 24 hours."""
    now = datetime.datetime.utcnow()
    db  = get_db()
    buckets = []

    for i in range(6):
        # bucket i: from (24-4*(i+1)) hours ago to (24-4*i) hours ago
        hours_end   = 24 - (i * 4)
        hours_start = hours_end - 4
        slot_end   = now - datetime.timedelta(hours=hours_start)
        slot_start = now - datetime.timedelta(hours=hours_end)
        label = f"{slot_start.strftime('%H:%M')}–{slot_end.strftime('%H:%M')}"
        count = 0
        if db is not None:
            try:
                count = db.reviews.count_documents(create_time_query(slot_start, slot_end))
            except Exception as e:
                app.logger.warning(f"engagement count error: {e}")
        else:
            with _mem_lock:
                for loc_data in _mem_cache.values():
                    for r in loc_data.get("all_reviews",[]):
                        ct = r.get("createTime","")
                        if ct:
                            try:
                                dt = datetime.datetime.fromisoformat(ct.replace("Z","+00:00")).replace(tzinfo=None)
                                if slot_start <= dt < slot_end:
                                    count+=1
                            except: pass
        buckets.append({
            "hour":  label,
            "count": count,
            "slotStart": slot_start.isoformat(),
            "slotEnd":   slot_end.isoformat(),
            "bucket":    i,
        })

    return jsonify({"hours": buckets, "generatedAt": now.isoformat(), "intervalHours": 4})

# ── 4. AI Intelligence ─────────────────────────────────────────────────────────
@app.route("/api/ai-intelligence")
def ai_intelligence():
    now     = datetime.datetime.utcnow()
    cut24h  = now - datetime.timedelta(hours=24)
    db      = get_db()
    star_map= {"ONE":1,"TWO":2,"THREE":3,"FOUR":4,"FIVE":5}

    reviews_24h = []
    if db is not None:
        try:
            docs = list(db.reviews.find(
                create_time_query(cut24h),
                {"starRating":1,"comment":1,"reviewer":1,"_numericRating":1,
                 "business_name":1,"Branch":1,"Cluster":1,"mail_id":1,"name":1}
            ))
            reviews_24h = [normalize_review_doc(d) for d in docs]
        except: pass

    if not reviews_24h:
        with _mem_lock:
            for loc_data in _mem_cache.values():
                for r in loc_data.get("all_reviews",[]):
                    ct = r.get("createTime","")
                    if ct:
                        try:
                            dt = datetime.datetime.fromisoformat(ct.replace("Z","+00:00")).replace(tzinfo=None)
                            if dt >= cut24h:
                                reviews_24h.append(r)
                        except: pass

    total24 = len(reviews_24h)
    critical_24h = []; positive_24h = []
    for r in reviews_24h:
        n = r.get("_numericRating") or star_map.get(str(r.get("starRating","")).upper(),0)
        r["_n"] = n
        if n<=2 and n>0: critical_24h.append(r)
        if n>=4:         positive_24h.append(r)

    branch_pos  = {}
    branch_crit = {}
    for r in positive_24h:
        b = r.get("_doctorBranch","Unknown")
        branch_pos[b] = branch_pos.get(b,0)+1
    for r in critical_24h:
        b = r.get("_doctorBranch","Unknown")
        branch_crit[b] = branch_crit.get(b,0)+1

    top_branch   = max(branch_pos,  key=branch_pos.get)  if branch_pos  else "N/A"
    worst_branch = max(branch_crit, key=branch_crit.get) if branch_crit else "N/A"

    insight_text = ""
    if OLLAMA_API_KEY and reviews_24h:
        sample_comments = "\n".join([
            f"- [{r.get('_n',0)}★] {r.get('reviewer',{}).get('displayName','')}: {str(r.get('comment',''))[:120]}"
            for r in reviews_24h[:20]
        ])
        insight_text = call_ollama(
            "You are a healthcare analytics AI. Analyse patient reviews and give 3 concise insights (each max 25 words). Focus on patterns, urgency, and actionable recommendations. Format: 1. ... 2. ... 3. ...",
            f"Last 24h reviews ({total24} total, {len(critical_24h)} critical):\n{sample_comments}"
        ) or ""

    return jsonify({
        "total24h":        total24,
        "critical24h":     len(critical_24h),
        "positive24h":     len(positive_24h),
        "topBranch":       top_branch,
        "topBranchCount":  branch_pos.get(top_branch,0),
        "worstBranch":     worst_branch,
        "worstCount":      branch_crit.get(worst_branch,0),
        "aiInsight":       insight_text,
        "positivePct":     round(len(positive_24h)/total24*100,1) if total24 else 0,
        "criticalPct":     round(len(critical_24h)/total24*100,1) if total24 else 0,
    })

# ── 5. Branch heatmap — with rating distribution counts ───────────────────────
@app.route("/api/branch-performance")
def branch_performance():
    """Branch heatmap with rating distribution counts per bracket."""
    now    = datetime.datetime.utcnow()
    cut24h = now - datetime.timedelta(hours=24)
    db     = get_db()

    branch_data = {}

    if db is not None:
        try:
            pipeline = [
                {"$addFields":{"_resolvedRating": resolved_numeric_rating_expr()}},
                {"$group":{
                    "_id": "$Branch",
                    "avgRating":    {"$avg": "$_resolvedRating"},
                    "totalReviews": {"$sum": 1},
                    "cluster":      {"$first": "$Cluster"},
                    # Rating distribution brackets
                    "cnt_4_5":  {"$sum":{"$cond":[{"$gte":["$_resolvedRating",4.5]},1,0]}},
                    "cnt_4_0":  {"$sum":{"$cond":[{"$and":[{"$gte":["$_resolvedRating",4.0]},{"$lt":["$_resolvedRating",4.5]}]},1,0]}},
                    "cnt_3_5":  {"$sum":{"$cond":[{"$and":[{"$gte":["$_resolvedRating",3.5]},{"$lt":["$_resolvedRating",4.0]}]},1,0]}},
                    "cnt_3_0":  {"$sum":{"$cond":[{"$and":[{"$gte":["$_resolvedRating",3.0]},{"$lt":["$_resolvedRating",3.5]}]},1,0]}},
                    "cnt_low":  {"$sum":{"$cond":[{"$lt":["$_resolvedRating",3.0]},1,0]}},
                }}
            ]
            docs = list(db.reviews.aggregate(pipeline))
            for d in docs:
                b = d["_id"] or "Unknown"
                branch_data[b] = {
                    "name":        b,
                    "avgRating":   round(d.get("avgRating",0) or 0, 2),
                    "totalReviews":d.get("totalReviews",0),
                    "cluster":     d.get("cluster",""),
                    "reviews24h":  0,
                    "crit24h":     0,
                    # Rating distribution
                    "cnt_4_5":     d.get("cnt_4_5",0),
                    "cnt_4_0":     d.get("cnt_4_0",0),
                    "cnt_3_5":     d.get("cnt_3_5",0),
                    "cnt_3_0":     d.get("cnt_3_0",0),
                    "cnt_low":     d.get("cnt_low",0),
                }

            # 24h counts
            pipe24 = [
                {"$match": create_time_query(cut24h)},
                {"$addFields":{"_resolvedRating": resolved_numeric_rating_expr()}},
                {"$group":{"_id":"$Branch","count":{"$sum":1},
                           "crit":{"$sum":{"$cond":[{"$lte":["$_resolvedRating",2]},1,0]}}}}
            ]
            for d in db.reviews.aggregate(pipe24):
                b = d["_id"] or "Unknown"
                if b in branch_data:
                    branch_data[b]["reviews24h"] = d["count"]
                    branch_data[b]["crit24h"]    = d["crit"]
        except Exception as e:
            app.logger.warning(f"Branch DB error: {e}")

    branches = sorted(branch_data.values(), key=lambda x: x["avgRating"])
    return jsonify({"branches": branches, "total": len(branches)})

# ── 6. Critical analysis ───────────────────────────────────────────────────────
@app.route("/api/critical-analysis")
def critical_analysis():
    now    = datetime.datetime.utcnow()
    cut24h = now - datetime.timedelta(hours=24)
    db     = get_db()

    crit_reviews = []
    if db is not None:
        try:
            docs = list(db.reviews.find(
                {"$and":[create_time_query(cut24h), query_critical_ratings()]},
                {"starRating":1,"comment":1,"reviewer":1,"_numericRating":1,"createTime":1,
                 "business_name":1,"Branch":1,"Cluster":1,"mail_id":1,"name":1}
            ).sort("createTime",-1).limit(50))
            crit_reviews = [normalize_review_doc(d) for d in docs]
        except: pass

    if not crit_reviews:
        with _mem_lock:
            for loc_data in _mem_cache.values():
                for r in loc_data.get("critical_reviews",[]):
                    ct = r.get("createTime","")
                    if ct:
                        try:
                            dt = datetime.datetime.fromisoformat(ct.replace("Z","+00:00")).replace(tzinfo=None)
                            if dt >= cut24h:
                                crit_reviews.append(r)
                        except:
                            crit_reviews.append(r)

    analysis_text = ""
    if crit_reviews:
        sample = "\n".join([
            f"- [{r.get('_numericRating',r.get('starRating','?'))}★] {r.get('reviewer',{}).get('displayName','Patient')}: {str(r.get('comment',''))[:150]}"
            for r in crit_reviews[:20]
        ])
        analysis_text = call_ollama(
            "You are a healthcare quality analyst. Analyse these 1-2 star patient reviews. Identify: 1) Main complaint themes 2) Most affected departments/branches 3) Urgent action items. Be specific and concise (max 150 words total).",
            f"Critical reviews (1-2★) from last 24h:\n{sample}"
        ) or f"Found {len(crit_reviews)} critical reviews in last 24h requiring attention."

    return jsonify({
        "totalCritical": len(crit_reviews),
        "analysis":      analysis_text,
        "reviews":       [{
            "name":         r.get("name",""),
            "doctorName":   r.get("_doctorName",""),
            "branch":       r.get("_doctorBranch",""),
            "rating":       r.get("_numericRating", r.get("starRating", 0)),
            "starRating":   r.get("starRating", str(r.get("_numericRating",0))),
            "reviewerName": r.get("reviewer",{}).get("displayName","Anonymous"),
            "comment":      r.get("comment",""),
            "createTime":   r.get("createTime",""),
        } for r in crit_reviews[:10]],
    })

# ── 7. Critical reviews — strictly last 24h ────────────────────────────────────
@app.route("/api/critical-reviews-24h")
def critical_reviews_24h():
    """Reviews with 1-2★ strictly from last 24h — DB only."""
    now    = datetime.datetime.utcnow()
    cut24h = now - datetime.timedelta(hours=24)
    db     = get_db()
    page   = int(request.args.get("page",1))
    limit  = int(request.args.get("limit",20))

    reviews = []
    total   = 0

    if db is not None:
        try:
            query = {
                "$and":[create_time_query(cut24h), query_critical_ratings()]
            }
            total   = db.reviews.count_documents(query)
            docs    = list(db.reviews.find(
                query,
                {"name":1,"business_name":1,"Branch":1,"Cluster":1,"mail_id":1,
                 "starRating":1,"comment":1,"createTime":1,
                 "_numericRating":1,"reviewer":1}
            ).sort("createTime",-1).skip((page-1)*limit).limit(limit))
            reviews = [normalize_review_doc(d) for d in docs]
        except Exception as e:
            app.logger.warning(f"critical-reviews-24h DB error: {e}")

    if not reviews and db is None:
        star_map={"ONE":1,"TWO":2,"THREE":3,"FOUR":4,"FIVE":5}
        with _mem_lock:
            for loc_data in _mem_cache.values():
                for r in loc_data.get("critical_reviews",[]):
                    ct = r.get("createTime","")
                    if ct:
                        try:
                            dt = datetime.datetime.fromisoformat(ct.replace("Z","+00:00")).replace(tzinfo=None)
                            if dt >= cut24h:
                                reviews.append(r)
                        except: pass
        total = len(reviews)
        start = (page-1)*limit
        reviews = reviews[start:start+limit]

    out = []
    for r in reviews:
        r.pop("_id", None)
        out.append(r)

    return jsonify({"criticalReviews":out,"total":total,"page":page,"limit":limit})

# ── 8. Generate AI reply ───────────────────────────────────────────────────────
@app.route("/api/generate-reply", methods=["POST"])
def generate_reply():
    body        = request.get_json() or {}
    comment     = body.get("comment","").strip()
    doctor_name = body.get("doctorName","").strip()
    reviewer    = body.get("reviewerName","").strip()

    if not comment:
        return jsonify({"error":"comment required"}), 400

    if doctor_name.startswith("accounts/") or doctor_name.startswith("locations/") or doctor_name.startswith("reviews/"):
        doctor_name = ""

    issue_summary = comment.split(".")[0].strip()
    if len(issue_summary) > 120:
        issue_summary = issue_summary[:117].rsplit(" ", 1)[0] + "..."

    text = call_ollama(
        f"You are a professional healthcare customer service agent for {doctor_name or 'our clinic'}. "
        "Read the patient's review carefully and write a personalized response using details from the review. "
        "Mention the reviewer by name if available, apologise for any negative experience, and invite them to contact the clinic directly. "
        "Keep it empathetic, specific, and under 100 words. Do not repeat any review resource ID or account path in the reply.",
        f"Reviewer name: {reviewer or 'Patient'}\nReview: \"{comment}\""
    )
    if not text:
        text = (f"Dear {reviewer or 'valued patient'}, thank you for your feedback about "
                f"{doctor_name or 'our clinic'}. We are sorry to hear that {issue_summary or 'your experience did not meet expectations'}. "
                "Please contact us directly so we can resolve this personally. "
                "Your feedback helps us improve.")
    return jsonify({"reply":text})

# ── 9. Reply to Google ─────────────────────────────────────────────────────────
@app.route("/api/reply", methods=["POST"])
def reply():
    body  = request.get_json() or {}
    email = body.get("email","").strip()
    text  = body.get("text","").strip()
    name  = body.get("name","").strip()

    if not email or not text or not name:
        return jsonify({"error":"email, text, and name are required"}), 400
    try:
        resp = requests.post(
            GMB_API_URL,
            json={"function":"replyreviews","email":email,"text":text,"name":name},
            timeout=15,
        )
        resp.raise_for_status()
        save_reply_to_db(name, text, email)
        return jsonify({"success":True,"data":resp.json()})
    except Exception as e:
        app.logger.error(f"Reply error: {e}")
        return jsonify({"error":str(e)}), 500

# ── Filters & doctors ──────────────────────────────────────────────────────────
@app.route("/api/filters")
def filters():
    df = get_csv_df()
    emails = sorted(df["mail_id"].dropna().unique().tolist()) if "mail_id" in df.columns else []
    phones = sorted(str(p) for p in df["phone"].dropna().unique().tolist()) if "phone" in df.columns else []
    names  = sorted(df["name"].dropna().unique().tolist()) if "name" in df.columns else []
    return jsonify({"emails":emails,"phones":phones,"names":names})

@app.route("/api/filter-options")
def filter_options():
    cluster_f = request.args.get("cluster","").strip()
    branch_f  = request.args.get("branch","").strip()
    db = get_db()
    if db is not None:
        try:
            query = {}
            if cluster_f:
                query["Cluster"] = {"$regex": f"^{cluster_f}$", "$options": "i"}
            clusters = sorted(db.doctors.distinct("Cluster", {})) if db is not None else []
            if branch_f:
                query["Branch"] = {"$regex": f"^{branch_f}$", "$options": "i"}
            locations = sorted(db.doctors.distinct("Branch", query)) if db is not None else []
            spec_query = query.copy()
            specs = sorted(db.doctors.distinct("primaryCategory", spec_query)) if db is not None else []
            return jsonify({"clusters":clusters,"locations":locations,"specialities":specs})
        except Exception:
            pass
    try:
        df = get_csv_df()
        clusters  = sorted(df["Cluster"].dropna().unique().tolist()) if "Cluster" in df.columns else []
        df_b      = df[df["Cluster"].fillna("").str.lower()==cluster_f.lower()] if cluster_f else df
        locations = sorted(df_b["Branch"].dropna().unique().tolist()) if "Branch" in df_b.columns else []
        df_s      = df_b[df_b["Branch"].fillna("").str.lower()==branch_f.lower()] if branch_f else df_b
        specs     = sorted(df_s["primaryCategory"].dropna().unique().tolist()) if "primaryCategory" in df_s.columns else []
        return jsonify({"clusters":clusters,"locations":locations,"specialities":specs})
    except:
        return jsonify({"clusters":[],"locations":[],"specialities":[]})

@app.route("/api/doctors")
def doctors():
    email_f  = request.args.get("email","").strip()
    phone_f  = request.args.get("phone","").strip()
    cluster_f= request.args.get("cluster","").strip()
    branch_f = request.args.get("branch","").strip()
    name_f   = request.args.get("name","").strip()
    page     = int(request.args.get("page",1))
    ps       = int(request.args.get("pageSize",50))

    db = get_db()
    if db is not None:
        try:
            query = {}
            if email_f:
                query["mail_id"] = email_f
            if phone_f:
                query["phone"] = phone_f
            if cluster_f:
                query["Cluster"] = {"$regex": f"^{cluster_f}$", "$options": "i"}
            if branch_f:
                query["Branch"] = {"$regex": f"^{branch_f}$", "$options": "i"}
            if name_f:
                query["name"] = {"$regex": name_f, "$options": "i"}
            projection = {"_id": 0, "Branch": 1, "Cluster": 1, "account": 1, "address": 1, "averageRating": 1,
                          "business_name": 1, "mail_id": 1, "name": 1, "newReviewUri": 1, "phone": 1,
                          "placeId": 1, "primaryCategory": 1, "profile_screenshot": 1, "totalReviewCount": 1,
                          "mapsUri": 1}
            docs = list(db.doctors.find(query, projection).sort("name", 1).skip((page-1)*ps).limit(ps))
            total = db.doctors.count_documents(query)
            return jsonify({"doctors":docs,"total":total,"page":page,
                            "pageSize":ps,"totalPages":math.ceil(total/ps) if total else 1})
        except Exception:
            pass

    all_docs = load_doctors()
    if email_f:   all_docs=[d for d in all_docs if str(d.get("mail_id") or "")==email_f]
    if phone_f:   all_docs=[d for d in all_docs if str(d.get("phone") or "").strip()==phone_f]
    if cluster_f: all_docs=[d for d in all_docs if str(d.get("Cluster") or "").lower()==cluster_f.lower()]
    if branch_f:  all_docs=[d for d in all_docs if str(d.get("Branch") or "").lower()==branch_f.lower()]
    if name_f:    all_docs=[d for d in all_docs if name_f.lower() in str(d.get("name") or "").lower()]

    total = len(all_docs)
    start = (page-1)*ps
    return jsonify({"doctors":all_docs[start:start+ps],"total":total,"page":page,
                    "pageSize":ps,"totalPages":math.ceil(total/ps) if total else 1})

@app.route("/api/reviews")
def reviews():
    email    = request.args.get("email","").strip()
    location = request.args.get("location","").strip()
    if not email or not location:
        return jsonify({"error":"email and location required"}),400
    entry = mem_get(location)
    if not entry or is_stale(location):
        revs = fetch_gmb_reviews(email,location)
        crit = classify_critical(revs)
        mem_set(location,revs,crit)
        entry = mem_get(location)
    all_revs = entry["all_reviews"] if entry else []
    page = int(request.args.get("page",1)); ps=int(request.args.get("pageSize",10))
    total=len(all_revs); start=(page-1)*ps
    return jsonify({"reviews":all_revs[start:start+ps],"total":total,"page":page,
                    "pageSize":ps,"totalPages":math.ceil(total/ps) if total else 1})

@app.route("/api/critical-reviews")
def critical_reviews():
    email    = request.args.get("email","").strip()
    location = request.args.get("location","").strip()
    if not email or not location:
        return jsonify({"error":"email and location required"}),400
    entry = mem_get(location)
    if not entry or is_stale(location):
        revs = fetch_gmb_reviews(email,location)
        crit = classify_critical(revs)
        mem_set(location,revs,crit)
        entry = mem_get(location)
    crit = entry["critical_reviews"] if entry else []
    return jsonify({"criticalReviews":crit,"total":len(crit),
                    "cachedAt":entry["fetched_at"] if entry else None})

@app.route("/api/stats")
def stats():
    email=request.args.get("email","").strip(); location=request.args.get("location","").strip()
    if not email or not location: return jsonify({"error":"email and location required"}),400
    entry=mem_get(location)
    if not entry or is_stale(location):
        revs=fetch_gmb_reviews(email,location); crit=classify_critical(revs)
        mem_set(location,revs,crit); entry=mem_get(location)
    all_revs=entry["all_reviews"] if entry else []
    crit=entry["critical_reviews"] if entry else []
    star_map={"ONE":1,"TWO":2,"THREE":3,"FOUR":4,"FIVE":5}
    ratings=[]
    for r in all_revs:
        raw=r.get("starRating",""); n=star_map.get(str(raw).upper(),0)
        if n==0:
            try: n=int(raw)
            except: n=0
        if n>0: ratings.append(n)
    total=len(all_revs); pos=sum(1 for r in ratings if r>=4); neu=sum(1 for r in ratings if r==3)
    avg=round(sum(ratings)/len(ratings),2) if ratings else 0
    return jsonify({"totalReviews":total,"criticalCount":len(crit),"positiveCount":pos,
                    "neutralCount":neu,"averageRating":avg,
                    "positivePct":round(pos/total*100,1) if total else 0,
                    "neutralPct":round(neu/total*100,1) if total else 0,
                    "criticalPct":round(len(crit)/total*100,1) if total else 0})

@app.route("/api/global-all-reviews")
def global_all_reviews():
    page=int(request.args.get("page",1)); ps=int(request.args.get("pageSize",12))
    cluster_f=request.args.get("cluster","").strip()
    location_f=request.args.get("location","").strip()
    search=request.args.get("search","").strip().lower()
    db = get_db()

    if db is not None:
        try:
            filters = []
            if cluster_f:
                filters.append({"$or": [
                    {"_doctorCluster": {"$regex": cluster_f, "$options":"i"}},
                    {"Cluster": {"$regex": cluster_f, "$options":"i"}},
                ]})
            if location_f:
                filters.append({"$or": [
                    {"_doctorBranch": {"$regex": location_f, "$options":"i"}},
                    {"Branch": {"$regex": location_f, "$options":"i"}},
                ]})
            if search:
                filters.append({"$or": [
                    {"business_name": {"$regex": search, "$options": "i"}},
                    {"Branch": {"$regex": search, "$options": "i"}},
                ]})
            query = {"$and": filters} if len(filters) > 1 else (filters[0] if filters else {})
            total = db.reviews.count_documents(query)
            docs  = list(db.reviews.find(query,
                {"name":1,"business_name":1,"Branch":1,"Cluster":1,"_doctorBranch":1,"_doctorCluster":1,"mail_id":1,
                 "starRating":1,"comment":1,"createTime":1,
                 "_numericRating":1,"reviewer":1}
            ).sort("createTime",-1).skip((page-1)*ps).limit(ps))
            reviews = []
            for r in docs:
                r.pop("_id",None)
                r = normalize_review_doc(r)
                reviews.append(r)
            return jsonify({"reviews":reviews,"total":total,"page":page,"pageSize":ps,
                            "totalPages":math.ceil(total/ps) if total else 1})
        except Exception as e:
            app.logger.error(f"global_all_reviews DB: {e}")

    # CSV fallback
    try:
        df=get_csv_df()
        cols=["_id","name","business_name","mail_id","Cluster","Branch",
              "averageRating","totalReviewCount","address","primaryCategory","account"]
        df=df[[c for c in cols if c in df.columns]]
        if cluster_f: df=df[df["Cluster"].fillna("").str.lower()==cluster_f.lower()]
        if location_f: df=df[df["Branch"].fillna("").str.lower()==location_f.lower()]
        if search:
            mask=(df.get("name",pd.Series(dtype=str)).fillna("").str.lower().str.contains(search)|
                  df.get("business_name",pd.Series(dtype=str)).fillna("").str.lower().str.contains(search)|
                  df.get("Branch",pd.Series(dtype=str)).fillna("").str.lower().str.contains(search))
            df=df[mask]
        if "totalReviewCount" in df.columns: df=df.sort_values("totalReviewCount",ascending=False)
        total=len(df); start=(page-1)*ps
        reviews=[]
        for r in df.iloc[start:start+ps].to_dict(orient="records"):
            rating=float(r.get("averageRating") or 0)
            reviews.append({"name":r.get("account") or r.get("_id") or "",
                "reviewer":{"displayName":r.get("name") or r.get("business_name") or "Unknown"},
                "starRating":str(round(rating)),"_numericRating":rating,
                "comment":"","_doctorName":r.get("name") or r.get("business_name") or "Unknown",
                "_doctorBranch":r.get("Branch") or "","_doctorCluster":r.get("Cluster") or "",
                "_doctorEmail":r.get("mail_id") or "","_totalReviews":r.get("totalReviewCount") or 0,
                   "_address":r.get("address") or "","_isCSVRecord":True,"_aiSuggestedReply":""})
        return jsonify({"reviews":reviews,"total":total,"page":page,"pageSize":ps,
                        "totalPages":math.ceil(total/ps) if total else 1})
    except Exception as e:
        app.logger.error(f"global_all_reviews CSV: {e}")
        return jsonify({"reviews":[],"total":0,"page":1,"pageSize":ps,"totalPages":1})

@app.route("/api/global-critical-reviews")
def global_critical_reviews():
    limit=int(request.args.get("limit",50)); page=int(request.args.get("page",1))
    db = get_db()

    if db is not None:
        try:
            query = query_critical_ratings()
            total = db.reviews.count_documents(query)
            docs  = list(db.reviews.find(query,
                {"name":1,"business_name":1,"Branch":1,"Cluster":1,"mail_id":1,
                 "starRating":1,"comment":1,"createTime":1,
                 "_numericRating":1,"reviewer":1}
            ).sort("createTime",-1).skip((page-1)*limit).limit(limit))
            reviews = [normalize_review_doc(d) for d in docs]
            return jsonify({"criticalReviews":reviews,"total":total,"page":page,
                            "totalPages":math.ceil(total/limit) if total else 1})
        except Exception as e:
            app.logger.error(f"global_critical DB: {e}")

    # CSV fallback
    try:
        df=get_csv_df()
        if "averageRating" not in df.columns:
            return jsonify({"criticalReviews":[],"total":0,"page":1,"totalPages":1})
        cdf=df[df["averageRating"].apply(lambda x: float(x)<3 if x is not None else False)].copy()
        cdf=cdf.sort_values("averageRating",ascending=True)
        total=len(cdf); start=(page-1)*limit
        reviews=[]
        for r in cdf.iloc[start:start+limit].to_dict(orient="records"):
            rating=float(r.get("averageRating") or 0)
            reviews.append({"name":r.get("account") or r.get("_id") or "",
                "reviewer":{"displayName":r.get("name") or r.get("business_name") or "Unknown"},
                "starRating":str(round(rating)),"_numericRating":rating,
                "comment":f"{r.get('primaryCategory') or 'Healthcare'} — {safe_int(r.get('totalReviewCount'))} reviews",
                "_doctorName":r.get("name") or r.get("business_name") or "Unknown",
                "_doctorBranch":r.get("Branch") or "","_doctorEmail":r.get("mail_id") or "",
                "_totalReviews":safe_int(r.get("totalReviewCount")),"_isCritical":True})
        return jsonify({"criticalReviews":reviews,"total":total,"page":page,
                        "totalPages":math.ceil(total/limit) if total else 1})
    except Exception as e:
        return jsonify({"criticalReviews":[],"total":0,"page":1,"totalPages":1})

@app.route("/api/analytics")
def analytics():
    cluster_f=request.args.get("cluster","").strip(); location_f=request.args.get("location","").strip()
    db = get_db()
    if db is not None:
        try:
            query = {}
            if cluster_f:
                query["Cluster"] = {"$regex": f"^{cluster_f}$", "$options": "i"}
            if location_f:
                query["Branch"] = {"$regex": f"^{location_f}$", "$options": "i"}
            projection = {"_id": 0, "name": 1, "business_name": 1, "Cluster": 1, "Branch": 1,
                          "averageRating": 1, "totalReviewCount": 1, "primaryCategory": 1,
                          "address": 1, "mail_id": 1}
            docs = list(db.doctors.find(query, projection))
            rows = []
            for r in docs:
                rating = float(r.get("averageRating") or 0)
                total_rv = safe_int(r.get("totalReviewCount"))
                rows.append({"doctorName":r.get("name") or r.get("business_name") or "Unknown",
                    "cluster":r.get("Cluster") or "—","location":r.get("Branch") or "—",
                    "speciality":r.get("primaryCategory") or "—","averageRating":round(rating,1),
                    "totalReviews":total_rv,"repliesDone":int(total_rv*0.6),
                    "pendingReplies":max(0,total_rv-int(total_rv*0.6)),
                    "address":str(r.get("address") or "")[:80],"email":r.get("mail_id") or ""})
            rows.sort(key=lambda x:x["totalReviews"],reverse=True)
            total_rv = sum(r["totalReviews"] for r in rows)
            critical_count = sum(1 for r in rows if 0 < r["averageRating"] < 3)
            positive_count = sum(1 for r in rows if r["averageRating"] >= 4)
            positive_pct = round(positive_count / len(rows) * 100, 1) if rows else 0
            return jsonify({"rows":rows,"totalReviews":total_rv,
                            "totalReplies":sum(r["repliesDone"] for r in rows),
                            "totalPending":sum(r["pendingReplies"] for r in rows),"doctorCount":len(rows),
                            "positivePct":positive_pct,"criticalCount":critical_count})
        except Exception:
            pass
    try:
        df=get_csv_df()
        cols=["_id","name","business_name","Cluster","Branch","averageRating",
              "totalReviewCount","primaryCategory","address","mail_id"]
        df=df[[c for c in cols if c in df.columns]].copy()
        if cluster_f: df=df[df["Cluster"].fillna("").str.lower()==cluster_f.lower()]
        if location_f: df=df[df["Branch"].fillna("").str.lower()==location_f.lower()]
        rows=[]
        for r in df.to_dict(orient="records"):
            rating=float(r.get("averageRating") or 0); total_rv=safe_int(r.get("totalReviewCount"))
            rows.append({"doctorName":r.get("name") or r.get("business_name") or "Unknown",
                "cluster":r.get("Cluster") or "—","location":r.get("Branch") or "—",
                "speciality":r.get("primaryCategory") or "—","averageRating":round(rating,1),
                "totalReviews":total_rv,"repliesDone":int(total_rv*0.6),
                "pendingReplies":max(0,total_rv-int(total_rv*0.6)),
                "address":str(r.get("address") or "")[:80],"email":r.get("mail_id") or ""})
        rows.sort(key=lambda x:x["totalReviews"],reverse=True)
        total_rv = sum(r["totalReviews"] for r in rows)
        critical_count = sum(1 for r in rows if 0 < r["averageRating"] < 3)
        positive_count = sum(1 for r in rows if r["averageRating"] >= 4)
        positive_pct = round(positive_count / len(rows) * 100, 1) if rows else 0
        return jsonify({"rows":rows,"totalReviews":total_rv,
                        "totalReplies":sum(r["repliesDone"] for r in rows),
                        "totalPending":sum(r["pendingReplies"] for r in rows),"doctorCount":len(rows),
                        "positivePct":positive_pct,"criticalCount":critical_count})
    except Exception:
        return jsonify({"rows":[],"totalReviews":0,"totalReplies":0,"totalPending":0,"doctorCount":0})

# ── Startup ────────────────────────────────────────────────────────────────────
def _start():
    threading.Thread(target=_preload, daemon=True).start()
    threading.Thread(target=_auto_refresh_loop, daemon=True).start()

if __name__ == "__main__":
    _start()
    app.run(host="0.0.0.0", port=2034, debug=True)

_start()  # for gunicorn