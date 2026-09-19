"""Turns what you said into an action.

- "open Spotify", "launch Discord": starts an installed app
- "youtube", "open reddit", "youtube.com": opens the site in Opera
- "search youtube for lo-fi music", "google weather in London": searches a site in Opera
- anything else: typed into Windows Search (the caller's fallback)
"""
import difflib
import json
import os
import re
import subprocess
import threading
import urllib.parse
from collections import namedtuple

Action = namedtuple("Action", "kind value label")  # kind: "url" | "app" | "type"

_LOCAL = os.environ.get("LOCALAPPDATA", "")
OPERA_PATHS = [
    os.path.join(_LOCAL, "Programs", "Opera", "opera.exe"),
    os.path.join(_LOCAL, "Programs", "Opera GX", "opera.exe"),
    r"C:\Program Files\Opera\opera.exe",
    r"C:\Program Files (x86)\Opera\opera.exe",
    r"C:\Program Files\Opera GX\opera.exe",
]

# name -> (display name, home page, search URL with {q} or None)
SITES = {
    "youtube": ("YouTube", "https://www.youtube.com", "https://www.youtube.com/results?search_query={q}"),
    "google": ("Google", "https://www.google.com", "https://www.google.com/search?q={q}"),
    "gmail": ("Gmail", "https://mail.google.com", None),
    "google maps": ("Google Maps", "https://www.google.com/maps", "https://www.google.com/maps/search/{q}"),
    "maps": ("Google Maps", "https://www.google.com/maps", "https://www.google.com/maps/search/{q}"),
    "google drive": ("Google Drive", "https://drive.google.com", None),
    "translate": ("Google Translate", "https://translate.google.com", None),
    "wikipedia": ("Wikipedia", "https://en.wikipedia.org", "https://en.wikipedia.org/w/index.php?search={q}"),
    "reddit": ("Reddit", "https://www.reddit.com", "https://www.reddit.com/search/?q={q}"),
    "github": ("GitHub", "https://github.com", "https://github.com/search?q={q}"),
    "amazon": ("Amazon", "https://www.amazon.com", "https://www.amazon.com/s?k={q}"),
    "ebay": ("eBay", "https://www.ebay.com", "https://www.ebay.com/sch/i.html?_nkw={q}"),
    "netflix": ("Netflix", "https://www.netflix.com", "https://www.netflix.com/search?q={q}"),
    "twitch": ("Twitch", "https://www.twitch.tv", "https://www.twitch.tv/search?term={q}"),
    "twitter": ("X", "https://x.com", "https://x.com/search?q={q}"),
    "x": ("X", "https://x.com", "https://x.com/search?q={q}"),
    "facebook": ("Facebook", "https://www.facebook.com", "https://www.facebook.com/search/top?q={q}"),
    "instagram": ("Instagram", "https://www.instagram.com", None),
    "linkedin": ("LinkedIn", "https://www.linkedin.com", None),
    "tiktok": ("TikTok", "https://www.tiktok.com", "https://www.tiktok.com/search?q={q}"),
    "pinterest": ("Pinterest", "https://www.pinterest.com", "https://www.pinterest.com/search/pins/?q={q}"),
    "chatgpt": ("ChatGPT", "https://chatgpt.com", None),
    "claude": ("Claude", "https://claude.ai", None),
    "stack overflow": ("Stack Overflow", "https://stackoverflow.com", "https://stackoverflow.com/search?q={q}"),
    "spotify web": ("Spotify", "https://open.spotify.com", "https://open.spotify.com/search/{q}"),
    "soundcloud": ("SoundCloud", "https://soundcloud.com", "https://soundcloud.com/search?q={q}"),
    "imdb": ("IMDb", "https://www.imdb.com", "https://www.imdb.com/find/?q={q}"),
    "weather": ("Weather", "https://www.google.com/search?q=weather", None),
    "news": ("Google News", "https://news.google.com", None),
}

OPEN_RE = re.compile(r"^(?:please\s+)?(?:open|launch|start|run|go to|goto|take me to|show me|switch to)\s+(?:the\s+)?")
IN_OPERA_RE = re.compile(r"\s+(?:in|on|with|using)\s+opera(?:\s+browser)?$")
SEARCH_ON_RE = re.compile(r"^(?:search|find|look up|look for)\s+(?:for\s+)?(.+?)\s+(?:on|in)\s+(.+)$")
SEARCH_SITE_FOR_RE = re.compile(r"^(?:search|find|look up)\s+(.+?)\s+for\s+(.+)$")
GOOGLE_RE = re.compile(r"^google\s+(?:for\s+)?(.+)$")
DOMAIN_RE = re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|org|net|io|gov|edu|co|tv|bg|uk|de|fr|me|app|dev|ai)(?:/\S*)?")

# Built-in tools that Windows does not list as Start apps: name -> program to run.
EXTRA_APPS = {
    "notepad": "notepad.exe", "paint": "mspaint.exe", "snipping tool": "snippingtool.exe",
    "command prompt": "cmd.exe", "powershell": "powershell.exe", "wordpad": "wordpad.exe",
}

_apps = []  # [(lowercase name, display name, app id)]
_apps_ready = threading.Event()


def normalize(text):
    said = text.lower().replace(" dot ", ".")
    said = re.sub(r"[^a-z0-9 .+#/-]", " ", said)
    said = re.sub(r"\s+", " ", said).strip(" .")
    return said


# --- installed apps -------------------------------------------------------------------

def _load_apps():
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json"],
            capture_output=True, text=True, timeout=60).stdout
        for item in json.loads(out):
            name = (item.get("Name") or "").strip()
            if name and item.get("AppID"):
                _apps.append((name.lower(), name, item["AppID"]))
    except Exception as error:
        print(f"Could not list installed apps: {error}")
    finally:
        known = {a[0] for a in _apps}
        for name, program in EXTRA_APPS.items():
            if name not in known:
                _apps.append((name, name.title(), "exe:" + program))
        _apps_ready.set()


def preload_apps():
    threading.Thread(target=_load_apps, daemon=True).start()


def _score(name, target):
    if name == target:
        return 1.0
    name_words, target_words = set(name.split()), set(target.split())
    if target_words and target_words <= name_words:
        return 0.92  # "chrome" -> "google chrome"
    return difflib.SequenceMatcher(None, name, target).ratio()


def best_app(target, cutoff):
    _apps_ready.wait(timeout=5)
    best, best_score = None, 0.0
    for lower, display, app_id in _apps:
        if any(w in lower for w in ("uninstall", "readme", "release notes", "help")):
            continue
        score = _score(lower, target)
        if score > best_score:
            best, best_score = (display, app_id), score
    return (best, best_score) if best_score >= cutoff else (None, 0.0)


def best_site(target, cutoff):
    best, best_score = None, 0.0
    for key in SITES:
        score = _score(key, target)
        if score > best_score:
            best, best_score = key, score
    return (best, best_score) if best_score >= cutoff else (None, 0.0)


def vocabulary():
    """Names Whisper should expect, to help it spell apps and sites correctly."""
    names = [v[0] for v in SITES.values()]
    names += [display for _, display, _ in _apps[:80]]
    seen, words = set(), []
    for n in names:
        if n.lower() not in seen:
            seen.add(n.lower())
            words.append(n)
    return ", ".join(words)[:600]


# --- turning a phrase into an action --------------------------------------------------

def _search_url(site, query):
    return SITES[site][2].format(q=urllib.parse.quote_plus(query))


def plan(text):
    """Decide what to do with the spoken text. None means "type it into Windows Search"."""
    said = IN_OPERA_RE.sub("", normalize(text))
    if not said:
        return None

    m = GOOGLE_RE.match(said)
    if m:
        return Action("url", _search_url("google", m.group(1)), "Searching Google")
    for regex, site_group, query_group in ((SEARCH_ON_RE, 2, 1), (SEARCH_SITE_FOR_RE, 1, 2)):
        m = regex.match(said)
        if m:
            site, _ = best_site(m.group(site_group), 0.8)
            if site and SITES[site][2]:
                return Action("url", _search_url(site, m.group(query_group)),
                              f"Searching {SITES[site][0]}")

    explicit = OPEN_RE.match(said)
    target = said[explicit.end():].strip() if explicit else said
    if DOMAIN_RE.fullmatch(target):
        return Action("url", "https://" + target, f"Opening {target}")

    if explicit or len(target.split()) <= 3:
        cutoff = 0.72 if explicit else 0.9
        app, app_score = best_app(target, cutoff)
        site, site_score = best_site(target, cutoff)
        if app and app_score >= site_score:
            return Action("app", app[1], f"Opening {app[0]}")
        if site:
            return Action("url", SITES[site][1], f"Opening {SITES[site][0]}")
    return None


def opera_path():
    return next((p for p in OPERA_PATHS if os.path.exists(p)), None)


def execute(action):
    if action.kind == "url":
        opera = opera_path()
        if opera:
            subprocess.Popen([opera, action.value])
        else:
            os.startfile(action.value)  # no Opera found: the default browser
    elif action.kind == "app" and action.value.startswith("exe:"):
        subprocess.Popen([action.value[4:]])
    elif action.kind == "app":
        subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + action.value])


def route(text, type_fallback):
    """Do what was said. Returns the short label to show on screen."""
    action = plan(text)
    if action is None:
        type_fallback(text)
        return "Typed"
    execute(action)
    return action.label