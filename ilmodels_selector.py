#!/usr/bin/env python3
"""
ILModels Selector Tool - בוחר דוגמניות
"""

import json
import os
import threading
import webbrowser
from functools import wraps
from urllib.parse import urljoin, urlparse

import requests as req
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template_string, request as flask_request, session, redirect, url_for

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "ilmodels-secret-2024")

# סיסמא — ניתן לשנות בהגדרות Railway
APP_PASSWORD = os.environ.get("APP_PASSWORD", "ilmodels2024")

LOGIN_HTML = """<!DOCTYPE html>
<html dir="rtl" lang="he">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ILModels – כניסה</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:'Segoe UI',Arial,sans-serif;background:#111;min-height:100vh;
       display:flex;align-items:center;justify-content:center}
  .box{background:#1a1a1a;border-radius:20px;padding:40px;width:min(360px,92vw);text-align:center}
  h1{color:#fff;font-size:1.4rem;margin-bottom:6px}
  p{color:#888;font-size:.85rem;margin-bottom:28px}
  input{width:100%;padding:13px 16px;border:1px solid #333;border-radius:10px;
        background:#222;color:#fff;font-size:1rem;margin-bottom:14px;direction:rtl;outline:none}
  input:focus{border-color:#25D366}
  button{width:100%;padding:13px;background:#25D366;color:#fff;border:none;
         border-radius:10px;font-size:1rem;font-weight:700;cursor:pointer}
  button:hover{background:#1fbb57}
  .err{color:#e57373;font-size:.85rem;margin-bottom:12px}
</style>
</head>
<body>
<div class="box">
  <h1>🎯 ILModels</h1>
  <p>בוחר דוגמניות</p>
  {% if error %}<div class="err">❌ סיסמא שגויה</div>{% endif %}
  <form method="post">
    <input type="password" name="password" placeholder="🔒 סיסמא" autofocus>
    <button type="submit">כניסה</button>
  </form>
</div>
</body>
</html>"""


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

BASE_URL = "https://www.ilmodel.com"
DEFAULT_MODELS_URL = "https://www.ilmodel.com/models"
ALLOWED_DOMAINS = ("www.ilmodel.com", "ilmodel.com", "www.ilmodels.co.il", "ilmodels.co.il")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "he-IL,he;q=0.9,en;q=0.8",
}

# ─────────────────────────────────────────────
# Scraping helpers
# ─────────────────────────────────────────────

def fetch_raw(url, timeout=8):
    """Return (response_text, status_code) or (None, 0) on error."""
    try:
        r = req.get(url, headers=HEADERS, timeout=timeout)
        return r.text, r.status_code
    except Exception as e:
        print(f"[FETCH] {url} → {e}")
        return None, 0


def fetch_soup(url):
    text, status = fetch_raw(url)
    if text and status < 400:
        return BeautifulSoup(text, "html.parser")
    return None


def find_image_in(obj, depth=0):
    """Recursively find the first image URL in a JSON object."""
    if depth > 6 or obj is None:
        return None
    if isinstance(obj, str) and obj.startswith("http") and any(
        ext in obj.lower() for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif")
    ):
        return obj
    if isinstance(obj, dict):
        # Squarespace classic image keys
        for key in ("assetUrl", "staticAssetPath", "originalAssetUrl", "src", "imageUrl", "url"):
            if key in obj and isinstance(obj[key], str) and obj[key].startswith("http"):
                return obj[key]
        # Squarespace 7.1 new-style images: systemDataId
        sys_id = obj.get("systemDataId")
        if sys_id and isinstance(sys_id, str):
            return f"https://images.squarespace-cdn.com/content/{sys_id}~rs=w-600,h-800,fo-auto"
        for v in obj.values():
            r = find_image_in(v, depth + 1)
            if r:
                return r
    return None


def extract_one_model(item, page_url):
    """Turn a single JSON item dict into a model dict, or return None."""
    if not isinstance(item, dict):
        return None

    url_id = item.get("urlId") or item.get("slug") or ""
    name = (
        item.get("title") or item.get("name") or item.get("label") or
        url_id.replace("-", " ").title()
    ).strip()

    full_url = item.get("fullUrl") or ""
    if not full_url and url_id:
        full_url = urljoin(BASE_URL, url_id)
    if full_url and not full_url.startswith("http"):
        full_url = urljoin(BASE_URL, full_url)

    thumb = (
        find_image_in(item.get("mainImage")) or
        find_image_in(item.get("backgroundSource")) or
        find_image_in(item.get("thumbnailImage")) or
        find_image_in(item.get("coverImage"))
    )

    if name and full_url:
        return {"name": name, "url": full_url, "image": thumb}
    return None


def items_from_json_data(data, page_url):
    """Extract model list from a Squarespace JSON blob."""
    if not isinstance(data, dict):
        return []

    models = []

    # ── Path A: top-level 'items' ──────────────────────────────────────────
    top_items = data.get("items") or []
    if isinstance(top_items, list) and top_items:
        print(f"    Path A (items): {len(top_items)} entries")
        for item in top_items:
            m = extract_one_model(item, page_url)
            if m:
                models.append(m)
        if models:
            print(f"    → {len(models)} models extracted")
            return models

    # ── Path B: collection.collections (Squarespace portfolio/gallery) ──────
    collection = data.get("collection") or {}
    if isinstance(collection, dict):
        sub_cols = collection.get("collections") or []
        if isinstance(sub_cols, list) and sub_cols:
            print(f"    Path B (collection.collections): {len(sub_cols)} entries")
            for item in sub_cols:
                m = extract_one_model(item, page_url)
                if m:
                    models.append(m)
            if models:
                print(f"    → {len(models)} models extracted")
                return models

    # ── Path C: scan entire tree for largest list ──────────────────────────
    def collect_lists(obj, depth=0):
        if depth > 4 or not isinstance(obj, dict):
            return []
        result = []
        for v in obj.values():
            if isinstance(v, list) and len(v) >= 2:
                result.append(v)
            elif isinstance(v, dict):
                result.extend(collect_lists(v, depth + 1))
        return result

    best = []
    for lst in collect_lists(data):
        if len(lst) > len(best) and isinstance(lst[0], dict):
            best = lst

    if best:
        print(f"    Path C (scan): list of {len(best)}")
        for item in best:
            m = extract_one_model(item, page_url)
            if m:
                models.append(m)

    return models


def items_from_script_tags(soup, page_url):
    """Extract Squarespace context JSON embedded in <script> tags."""
    import re as _re
    models = []
    patterns = [
        r'Static\.SQUARESPACE_CONTEXT\s*=\s*(\{.+?\});?\s*\n',
        r'window\.SQUARESPACE_CONTEXT\s*=\s*(\{.+?\});',
        r'<script[^>]*type=["\']application/json["\'][^>]*>(\{.+?\})</script>',
    ]
    for script in soup.find_all("script"):
        text = script.string or ""
        if not text:
            continue
        for pat in patterns:
            m = _re.search(pat, text, _re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group(1))
                    found = items_from_json_data(data, page_url)
                    if found:
                        print(f"  ✓ Found {len(found)} items in embedded script JSON")
                        models.extend(found)
                except Exception:
                    pass
    return models


def items_from_html(soup, page_url):
    """HTML link scraping fallback."""
    models = []
    seen = set()
    skip_paths = {"/", ""} | {"/blog", "/news", "/contact", "/about",
                               "/shop", "/cart", "/account", "/search",
                               "/tag", "/category", "/faq", "/privacy", "/terms"}
    selectors = [
        "article.portfolio-grid-item", ".portfolio-grid-item",
        ".portfolio-item", ".summary-item", ".gallery-grid-item",
        "[data-collection-item-id]", "article[class*='portfolio']", "li[class*='item'] a",
    ]
    containers = []
    for sel in selectors:
        found = soup.select(sel)
        if len(found) >= 2:
            containers = found
            print(f"  Using selector: {sel} ({len(found)} items)")
            break
    if not containers:
        containers = [a for a in soup.find_all("a", href=True) if a.find("img")]

    for el in containers:
        a = el if el.name == "a" else el.find("a", href=True)
        img = el.find("img")
        if not a:
            continue
        full_url = urljoin(page_url, a.get("href", ""))
        parsed = urlparse(full_url)
        if parsed.netloc not in ALLOWED_DOMAINS or full_url in seen:
            continue
        path = parsed.path.rstrip("/")
        if path in skip_paths or any(path.startswith(p) for p in skip_paths if len(p) > 1):
            continue
        title_el = el.find(["h1","h2","h3","h4","p"],
                            class_=lambda c: c and any(k in c for k in ("title","name","heading","label")))
        name = (
            (title_el and title_el.get_text(strip=True)) or
            (img and img.get("alt", "").strip()) or
            a.get_text(strip=True) or
            path.split("/")[-1].replace("-", " ").title()
        ).strip()
        if not name or len(name) > 100:
            continue
        img_src = None
        if img:
            img_src = (img.get("src") or img.get("data-src") or
                       img.get("data-lazy-src") or img.get("data-original") or "").strip()
            if not img_src.startswith("http"):
                img_src = None
        seen.add(full_url)
        models.append({"name": name, "url": full_url, "image": img_src})
    return models


def scrape_models(target_url=None):
    """Try three strategies in order; return first that yields results."""
    if not target_url:
        target_url = DEFAULT_MODELS_URL
    print(f"\n[Scraper] ── {target_url}")

    # Strategy 1: Squarespace ?format=json API
    sep = "&" if "?" in target_url else "?"
    json_url = target_url + sep + "format=json"
    text, status = fetch_raw(json_url, timeout=8)
    print(f"  JSON API → HTTP {status}, {len(text or '')} bytes")
    if text and status < 400:
        try:
            data = json.loads(text)
            models = items_from_json_data(data, target_url)
            if models:
                print(f"  ✓ Strategy 1 (JSON API): {len(models)} models")
                return models
        except Exception as e:
            print(f"  JSON parse error: {e}")

    # Strategy 2: Embedded script JSON
    soup = fetch_soup(target_url)
    if soup:
        models = items_from_script_tags(soup, target_url)
        if models:
            print(f"  ✓ Strategy 2 (Script tags): {len(models)} models")
            return models

        # Strategy 3: HTML link scraping
        models = items_from_html(soup, target_url)
        if models:
            print(f"  ✓ Strategy 3 (HTML scraping): {len(models)} models")
            return models

    print("  ✗ All strategies failed — 0 models found")
    return []


# ─────────────────────────────────────────────
# HTML Template
# ─────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html dir="rtl" lang="he">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ILModels – בוחר דוגמניות</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:'Segoe UI',Arial,sans-serif;background:#f2f2f2;color:#222;min-height:100vh}

  header{
    background:#111;color:#fff;
    padding:16px 28px;
    display:flex;align-items:center;justify-content:space-between;
    position:sticky;top:0;z-index:100;
    box-shadow:0 2px 12px rgba(0,0,0,.35)
  }
  header h1{font-size:1.25rem;font-weight:700;letter-spacing:.5px}
  .header-right{display:flex;align-items:center;gap:12px}
  .badge{
    background:#25D366;color:#fff;
    padding:5px 14px;border-radius:20px;
    font-size:.85rem;font-weight:700;min-width:80px;text-align:center
  }
  .btn{
    border:none;cursor:pointer;border-radius:22px;
    padding:9px 18px;font-size:.875rem;font-weight:600;
    transition:opacity .15s,background .15s
  }
  .btn:disabled{opacity:.4;cursor:not-allowed}
  .btn-clear{background:#333;color:#ccc}
  .btn-clear:hover:not(:disabled){background:#444}
  .btn-wa{background:#25D366;color:#fff;display:flex;align-items:center;gap:6px}
  .btn-wa:hover:not(:disabled){background:#1fbb57}

  .statusbar{max-width:1200px;margin:24px auto 0;padding:0 20px}
  .statusbar-inner{
    background:#fff;border-radius:10px;padding:12px 18px;
    display:flex;align-items:center;gap:10px;
    box-shadow:0 1px 4px rgba(0,0,0,.08)
  }
  .dot{width:9px;height:9px;border-radius:50%;background:#25D366;flex-shrink:0}
  .dot.loading{background:#f0a500;animation:pulse 1s infinite alternate}
  .dot.error{background:#e53935}
  @keyframes pulse{to{opacity:.3}}

  /* error banner */
  #errBanner{
    display:none;
    max-width:1200px;margin:14px auto 0;padding:0 20px
  }
  #errBanner .inner{
    background:#fff3f3;border:1px solid #ffcdd2;border-radius:10px;
    padding:14px 18px;color:#c62828;font-size:.9rem;line-height:1.6
  }
  #errBanner code{background:#fde;border-radius:4px;padding:2px 6px;font-size:.85rem}

  .grid-wrap{max-width:1200px;margin:20px auto 40px;padding:0 20px}
  .grid{
    display:grid;
    grid-template-columns:repeat(auto-fill,minmax(170px,1fr));
    gap:18px
  }

  .card{
    background:#fff;border-radius:14px;overflow:hidden;
    cursor:pointer;position:relative;
    border:3px solid transparent;
    transition:transform .18s,box-shadow .18s,border-color .18s;
    box-shadow:0 2px 8px rgba(0,0,0,.08)
  }
  .card:hover{transform:translateY(-4px);box-shadow:0 8px 22px rgba(0,0,0,.14)}
  .card.sel{border-color:#25D366}

  .check{
    position:absolute;top:9px;left:9px;
    width:30px;height:30px;border-radius:50%;
    background:#25D366;
    display:flex;align-items:center;justify-content:center;
    opacity:0;transition:opacity .18s;
    box-shadow:0 2px 8px rgba(37,211,102,.45)
  }
  .card.sel .check{opacity:1}
  .check svg{width:16px;height:16px;fill:#fff}

  .img-wrap{width:100%;aspect-ratio:3/4;overflow:hidden;background:#e4e4e4;position:relative}
  .img-wrap img{width:100%;height:100%;object-fit:cover;display:block;transition:transform .3s}
  .card:hover .img-wrap img{transform:scale(1.04)}
  .img-placeholder{
    width:100%;height:100%;display:flex;
    align-items:center;justify-content:center;
    font-size:3rem;color:#bbb
  }

  .card-info{padding:11px 12px}
  .card-name{font-size:.88rem;font-weight:700;color:#111;margin-bottom:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

  .centered{text-align:center;padding:60px 20px;color:#888}
  .spinner{
    width:42px;height:42px;border:4px solid #e0e0e0;
    border-top-color:#25D366;border-radius:50%;
    animation:spin .75s linear infinite;margin:0 auto 18px
  }
  @keyframes spin{to{transform:rotate(360deg)}}

  .overlay{
    display:none;position:fixed;inset:0;
    background:rgba(0,0,0,.55);z-index:200;
    align-items:center;justify-content:center
  }
  .overlay.open{display:flex}
  .modal{
    background:#fff;border-radius:18px;padding:28px;
    width:min(500px,92vw);direction:rtl
  }
  .modal h2{font-size:1.15rem;margin-bottom:16px;display:flex;align-items:center;gap:8px}
  .modal textarea{
    width:100%;height:240px;border:1px solid #ddd;border-radius:10px;
    padding:13px;font-size:.88rem;line-height:1.7;resize:vertical;
    font-family:inherit;direction:rtl;color:#222
  }
  .modal-actions{display:flex;gap:10px;margin-top:14px;flex-wrap:wrap}
  .m-btn{
    flex:1;min-width:120px;border:none;border-radius:9px;
    padding:12px;font-size:.9rem;font-weight:600;cursor:pointer;
    transition:background .15s;text-align:center;text-decoration:none;
    display:flex;align-items:center;justify-content:center;gap:5px
  }
  .m-copy{background:#111;color:#fff}
  .m-copy:hover{background:#333}
  .m-wa{background:#25D366;color:#fff}
  .m-wa:hover{background:#1fbb57}
  .m-close{background:#f2f2f2;color:#555}
  .m-close:hover{background:#e0e0e0}

  /* template buttons */
  .tmpl-btn{
    background:#f0f0f0;color:#555;border:2px solid transparent;
    border-radius:16px;padding:5px 13px;font-size:.8rem;font-weight:600;
    cursor:pointer;transition:all .15s
  }
  .tmpl-btn:hover{background:#e4e4e4}
  .tmpl-btn.active{background:rgba(37,211,102,.12);color:#1a9e4a;border-color:#25D366}

  .btn-refresh{background:transparent;color:#888;border:1px solid #555;padding:8px 15px;border-radius:20px;cursor:pointer;font-size:.8rem;transition:all .2s}
  .btn-refresh:hover{color:#fff;border-color:#aaa}

  .search-wrap{padding:0 20px;max-width:1200px;margin:12px auto 0}
  .search-input{
    width:100%;padding:11px 16px;border:1px solid #ddd;border-radius:10px;
    font-size:.95rem;background:#fff;direction:rtl;
    outline:none;box-shadow:0 1px 4px rgba(0,0,0,.06)
  }
  .search-input:focus{border-color:#25D366}

  .cat-header{
    font-size:1.1rem;font-weight:800;color:#222;
    padding:18px 0 8px;
    border-bottom:3px solid #25D366;
    margin-bottom:4px;
    display:flex;align-items:center;gap:10px
  }
  .cat-count{
    background:#25D366;color:#fff;
    font-size:.75rem;font-weight:700;
    padding:3px 10px;border-radius:12px
  }

  .cat-chip{
    display:inline-flex;align-items:center;gap:6px;
    padding:7px 14px;border-radius:20px;cursor:pointer;
    background:#f0f0f0;color:#555;font-size:.85rem;font-weight:600;
    border:2px solid transparent;transition:all .15s;user-select:none
  }
  .cat-chip input{display:none}
  .cat-chip.selected{background:rgba(37,211,102,.12);color:#1a9e4a;border-color:#25D366}
  .cat-chip:hover{background:#e8e8e8}

  /* big load button */
  #btnLoad{
    background:#25D366;color:#fff;border:none;
    border-radius:12px;padding:12px 28px;
    font-size:1rem;font-weight:700;cursor:pointer;
    transition:background .15s;white-space:nowrap
  }
  #btnLoad:hover{background:#1fbb57}
  #btnLoad:disabled{opacity:.4;cursor:not-allowed}
</style>
</head>
<body>

<header>
  <h1>🎯 ILModels – בוחר דוגמניות</h1>
  <div class="header-right">
    <span class="badge" id="badge">0 נבחרו</span>
    <button class="btn btn-clear" onclick="clearAll()">נקה הכל</button>
    <button class="btn btn-wa" id="btnWa" onclick="openModal()" disabled>📱 צור הודעה</button>
  </div>
</header>

<div class="statusbar">
  <div class="statusbar-inner">
    <div class="dot" id="dot"></div>
    <span id="statusTxt">מחכה לטעינה...</span>
    <button class="btn-refresh" onclick="loadSelected()" style="margin-right:auto">🔄 רענן</button>
  </div>
</div>

<!-- Error banner (shown when server is down) -->
<div id="errBanner">
  <div class="inner">
    ⚠️ <strong>השרת לא מגיב.</strong> פתח Terminal והרץ:<br>
    <code>cd ~/Desktop && python3 ilmodels_selector.py</code><br>
    אחרי שהשרת עולה, חזור לכאן ולחץ <strong>⬇️ טען</strong>.
  </div>
</div>

<div class="search-wrap">
  <div style="background:#fff;border-radius:12px;padding:16px 20px;box-shadow:0 1px 4px rgba(0,0,0,.08);margin-bottom:12px">
    <div style="font-size:.85rem;font-weight:700;color:#555;margin-bottom:12px">📂 בחר קטגוריות לטעינה:</div>
    <div style="display:flex;flex-wrap:wrap;gap:10px;margin-bottom:14px" id="catList">
      <div class="cat-chip" onclick="toggleCat(this,'https://www.ilmodel.com/models')">👩 Women</div>
      <div class="cat-chip" onclick="toggleCat(this,'https://www.ilmodel.com/men')">👨 Men</div>
      <div class="cat-chip" onclick="toggleCat(this,'https://www.ilmodel.com/plus-size')">🌟 Plus Size</div>
      <div class="cat-chip" onclick="toggleCat(this,'https://www.ilmodel.com/development')">🌱 Development</div>
      <div class="cat-chip" onclick="toggleCat(this,'https://www.ilmodel.com/classic-women')">💎 Classic Women</div>
      <div class="cat-chip" onclick="selectAllCats()" style="border-color:#aaa;color:#555">✓ בחר הכל</div>
    </div>
    <div style="display:flex;gap:10px;align-items:center">
      <input class="search-input" type="url" placeholder="🔗 הוסף כתובת נוספת..." id="customUrl" style="flex:1;margin:0">
      <button onclick="addCustomUrl()" style="background:#333;color:#fff;border:none;border-radius:8px;padding:9px 14px;cursor:pointer;white-space:nowrap;font-size:.85rem">+ הוסף</button>
      <button id="btnLoad" onclick="loadSelected()">⬇️ טען דוגמניות</button>
    </div>
  </div>
  <input class="search-input" type="text" placeholder="🔍 חיפוש לפי שם..." oninput="filterModels(this.value)" id="searchBox">
</div>

<div class="grid-wrap">
  <div class="grid" id="grid">
    <div class="centered" style="grid-column:1/-1;color:#aaa;font-size:1.1rem">
      ☝️ לחץ <strong style="color:#25D366">⬇️ טען דוגמניות</strong> כדי להתחיל
    </div>
  </div>
</div>

<!-- Modal -->
<div class="overlay" id="overlay" onclick="if(event.target===this)closeModal()">
  <div class="modal">
    <h2>📱 הודעת וואטסאפ</h2>

    <!-- Template picker -->
    <div style="margin-bottom:10px">
      <div style="font-size:.8rem;color:#888;margin-bottom:6px">תבנית הודעה:</div>
      <div style="display:flex;flex-wrap:wrap;gap:6px" id="templateBtns">
        <button class="tmpl-btn active" onclick="applyTemplate('standard',this)">📋 רגיל</button>
        <button class="tmpl-btn" onclick="applyTemplate('short',this)">⚡ קצר</button>
        <button class="tmpl-btn" onclick="applyTemplate('formal',this)">👔 פורמלי</button>
        <button class="tmpl-btn" onclick="applyTemplate('casual',this)">😊 קז'ואל</button>
      </div>
    </div>

    <!-- Editable textarea -->
    <textarea id="msgBox" placeholder="ערוך את ההודעה כאן..."></textarea>
    <div style="font-size:.75rem;color:#aaa;text-align:left;margin-top:4px">ניתן לערוך את ההודעה לפני השליחה</div>

    <div class="modal-actions">
      <button class="m-btn m-copy" onclick="copyMsg()">📋 העתק</button>
      <a class="m-btn m-wa" id="waLink" href="#" target="_blank" onclick="updateWaLink()">💬 פתח WhatsApp</a>
      <button class="m-btn m-close" onclick="closeModal()">✕ סגור</button>
    </div>
  </div>
</div>

<script>
let allModels = [];
let byCategory = [];
let selected   = new Set();
let isLoading  = false;

const CAT_NAMES = {
  'https://www.ilmodel.com/models':         '👩 Women',
  'https://www.ilmodel.com/men':            '👨 Men',
  'https://www.ilmodel.com/plus-size':      '🌟 Plus Size',
  'https://www.ilmodel.com/development':    '🌱 Development',
  'https://www.ilmodel.com/classic-women':  '💎 Classic Women',
};

let checkedUrls = new Set(); // nothing selected by default

function selectAllCats() {
  document.querySelectorAll('#catList .cat-chip[onclick*="toggleCat"]').forEach(el => {
    const match = el.getAttribute('onclick').match(/'([^']+)'\)/);
    if (match) {
      checkedUrls.add(match[1]);
      el.classList.add('selected');
    }
  });
}

function toggleCat(el, url) {
  if (checkedUrls.has(url)) {
    checkedUrls.delete(url);
    el.classList.remove('selected');
  } else {
    checkedUrls.add(url);
    el.classList.add('selected');
  }
}

function addCustomUrl() {
  const inp = document.getElementById('customUrl');
  const url = inp.value.trim();
  if (!url) return;
  const list = document.getElementById('catList');
  const div = document.createElement('div');
  const slug = url.split('/').pop() || url;
  div.className = 'cat-chip selected';
  div.textContent = '🔗 ' + slug;
  div.onclick = function() { toggleCat(div, url); };
  list.appendChild(div);
  checkedUrls.add(url);
  inp.value = '';
}

// ── Load ──────────────────────────────────────────
async function loadSelected() {
  if (isLoading) return;
  const urls = [...checkedUrls];
  if (!urls.length) { setStatus('בחר לפחות קטגוריה אחת', false); return; }

  isLoading = true;
  document.getElementById('btnLoad').disabled = true;
  document.getElementById('errBanner').style.display = 'none';

  allModels = [];
  byCategory = [];
  selected.clear();
  updateBadge();
  setStatus(`טוען ${urls.length} קטגוריות...`, true);
  document.getElementById('grid').innerHTML =
    '<div class="centered" style="grid-column:1/-1"><div class="spinner"></div><div style="margin-top:8px">מוריד דוגמניות מהאתר...</div></div>';

  const seenUrls = new Set();
  let serverOk = false;

  for (const url of urls) {
    const catName = CAT_NAMES[url] || ('🔗 ' + (url.split('/').pop() || url));
    setStatus(`טוען: ${catName}...`, true);
    try {
      const r = await fetch('/api/models?url=' + encodeURIComponent(url));
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const d = await r.json();
      serverOk = true;
      const models = (d.models || []).filter(m => {
        if (seenUrls.has(m.url)) return false;
        seenUrls.add(m.url);
        return true;
      }).map(m => ({ ...m, category: catName }));
      if (models.length) {
        byCategory.push({ catName, catUrl: url, models });
        allModels.push(...models);
      }
    } catch(e) {
      console.error('Error loading', url, e);
      if (!serverOk) {
        // Server probably not running
        showServerError();
        isLoading = false;
        document.getElementById('btnLoad').disabled = false;
        setStatus('שגיאה – השרת לא מגיב', false, true);
        document.getElementById('grid').innerHTML =
          '<div class="centered" style="grid-column:1/-1;color:#e53935">⚠️ לא ניתן להתחבר לשרת. ראה הוראות למטה.</div>';
        return;
      }
    }
  }

  renderGrouped(byCategory);
  if (allModels.length) {
    setStatus(`נמצאו ${allModels.length} דוגמניות – לחץ על דוגמנית לבחור`, false);
  } else {
    setStatus('לא נמצאו דוגמניות. בדוק את הכתובות.', false);
  }
  isLoading = false;
  document.getElementById('btnLoad').disabled = false;
}

function showServerError() {
  document.getElementById('errBanner').style.display = 'block';
}

function setStatus(txt, loading, err) {
  document.getElementById('statusTxt').textContent = txt;
  const dot = document.getElementById('dot');
  dot.className = 'dot' + (loading ? ' loading' : err ? ' error' : '');
}

// ── Card HTML ─────────────────────────────────────
function cardHtml(m) {
  const isSel = selected.has(m.url);
  return `
<div class="card${isSel?' sel':''}" onclick="toggle('${esc(m.url)}',this)" title="${esc(m.name)}">
  <div class="check"><svg viewBox="0 0 24 24"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg></div>
  <div class="img-wrap">
    ${m.image
      ? `<img src="${esc(m.image)}" alt="${esc(m.name)}" loading="lazy" onerror="this.parentElement.innerHTML='<div class=\\'img-placeholder\\'>👤</div>'">`
      : `<div class="img-placeholder">👤</div>`
    }
  </div>
  <div class="card-info">
    <div class="card-name">${esc(m.name)}</div>
  </div>
</div>`;
}

function renderGrouped(categories) {
  const grid = document.getElementById('grid');
  if (!categories.length) {
    grid.innerHTML = '<div class="centered" style="grid-column:1/-1">לא נמצאו תוצאות</div>';
    return;
  }
  let html = '';
  for (const cat of categories) {
    html += `<div class="cat-header" style="grid-column:1/-1">${esc(cat.catName)} <span class="cat-count">${cat.models.length}</span></div>`;
    html += cat.models.map(m => cardHtml(m)).join('');
  }
  grid.innerHTML = html;
}

function renderGrid(models) {
  const grid = document.getElementById('grid');
  if (!models.length) {
    grid.innerHTML = '<div class="centered" style="grid-column:1/-1">לא נמצאו תוצאות</div>';
    return;
  }
  grid.innerHTML = models.map(m => cardHtml(m)).join('');
}

function toggle(url, card) {
  if (selected.has(url)) { selected.delete(url); card.classList.remove('sel'); }
  else                   { selected.add(url);    card.classList.add('sel'); }
  updateBadge();
}

function clearAll() {
  selected.clear();
  document.querySelectorAll('.card.sel').forEach(c => c.classList.remove('sel'));
  updateBadge();
}

function updateBadge() {
  const n = selected.size;
  document.getElementById('badge').textContent = n + ' נבחרו';
  document.getElementById('btnWa').disabled = n === 0;
}

function filterModels(q) {
  if (!q.trim()) { renderGrouped(byCategory); }
  else {
    const filtered = allModels.filter(m => m.name.toLowerCase().includes(q.toLowerCase()));
    renderGrid(filtered);
  }
}

// ── Templates ─────────────────────────────────
const NL = '\\n';
const TEMPLATES = {
  standard: (names) =>
    'שלום! 👋' + NL + 'הנה הקישורים לדוגמניות שנבחרו עבורך:' + NL + NL + names +
    'לפרטים נוספים – ILModels 🌟' + NL + 'www.ilmodels.co.il',

  short: (names) =>
    'היי! 😊' + NL + 'בחרתי עבורך:' + NL + NL + names + 'ILModels – www.ilmodels.co.il',

  formal: (names) =>
    'שלום רב,' + NL + 'בהמשך לשיחתנו, מצורפים קישורים לפרופילי הדוגמניות המתאימות:' + NL + NL +
    names + 'בברכה,' + NL + 'צוות ILModels' + NL + 'www.ilmodels.co.il',

  casual: (names) =>
    'היי! 🌟' + NL + 'אלו הבנות שחשבתי עליהן בשבילך:' + NL + NL + names +
    'תגיד/י מה את/ה חושב/ת! 😊' + NL + 'ILModels',
};

let currentTemplate = 'standard';

function buildLinks() {
  return allModels.filter(m => selected.has(m.url))
    .map(m => '✨ ' + m.name + NL + m.url + NL + NL).join('');
}

function applyTemplate(key, btn) {
  currentTemplate = key;
  document.querySelectorAll('.tmpl-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  const links = buildLinks();
  document.getElementById('msgBox').value = TEMPLATES[key](links);
  updateWaLink();
}

function openModal() {
  const links = buildLinks();
  document.getElementById('msgBox').value = TEMPLATES[currentTemplate](links);
  // reset template buttons
  document.querySelectorAll('.tmpl-btn').forEach((b,i) => b.classList.toggle('active', i===0));
  currentTemplate = 'standard';
  updateWaLink();
  document.getElementById('overlay').classList.add('open');
}

function updateWaLink() {
  const text = document.getElementById('msgBox').value;
  document.getElementById('waLink').href = 'https://wa.me/?text=' + encodeURIComponent(text);
}

function closeModal() { document.getElementById('overlay').classList.remove('open'); }

function copyMsg() {
  updateWaLink();
  navigator.clipboard.writeText(document.getElementById('msgBox').value).then(() => {
    const b = document.querySelector('.m-copy');
    b.textContent = '✅ הועתק!';
    setTimeout(() => b.textContent = '📋 העתק', 2200);
  });
}

function esc(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

document.addEventListener('keydown', e => { if (e.key==='Escape') closeModal(); });

// Check server health on page load
(async function checkServer() {
  try {
    const r = await fetch('/api/health');
    if (r.ok) {
      setStatus('השרת פועל ✓ – לחץ ⬇️ טען דוגמניות', false);
    }
  } catch(e) {
    setStatus('השרת לא מגיב – ראה הוראות', false, true);
    showServerError();
  }
})();
</script>
</body>
</html>"""


# ─────────────────────────────────────────────
# Flask routes
# ─────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    error = False
    if flask_request.method == "POST":
        if flask_request.form.get("password") == APP_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("index"))
        error = True
    return render_template_string(LOGIN_HTML, error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    return render_template_string(HTML)


@app.route("/api/health")
def api_health():
    return jsonify({"status": "ok"})


@app.route("/api/models")
@login_required
def api_models():
    target = flask_request.args.get("url", DEFAULT_MODELS_URL)
    models = scrape_models(target_url=target)
    return jsonify({"models": models, "count": len(models)})


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 8080))
    IS_LOCAL = not os.environ.get("RAILWAY_ENVIRONMENT")

    banner = f"""
╔══════════════════════════════════════════╗
║   🎯  ILModels – בוחר דוגמניות         ║
╠══════════════════════════════════════════╣
║  כתובת:  http://localhost:{PORT}            ║
║  לעצור:  Ctrl + C                        ║
╚══════════════════════════════════════════╝
"""
    print(banner)
    if IS_LOCAL:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    app.run(debug=False, port=PORT, host="0.0.0.0")
