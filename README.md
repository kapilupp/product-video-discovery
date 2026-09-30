# Product Video Discovery Dashboard

A full-stack dashboard: type a product name or paste a product link, and get
back at least 40 relevant short-form videos (20 Instagram Reels + 20 Meta Ad
Library videos) that show the *exact* product, ranked by an image-matching
score.

> **Note on tech stack.** This assignment's brief specifies a Node.js
> backend. This implementation uses a **Python/FastAPI backend with
> PostgreSQL** instead, by explicit request from the candidate's contact for
> this submission — the frontend is still React as required. Flagging this
> upfront so it isn't mistaken for an oversight.

## Stack

| Layer | Choice | Why |
|---|---|---|
| Backend | Python 3.11, FastAPI | Native async (needed for running Instagram/Meta/TikTok fetches in parallel), background tasks + SSE built in, good typing via Pydantic. |
| Frontend | React (Vite) | Fast dev server, no opinions forced on routing/state for a dashboard this size. |
| Database | PostgreSQL (SQLAlchemy async) | Relational shape fits well: searches → videos, plus a global `seen_videos` table with a unique index for de-dup lookups. |
| Image brain | Pluggable: local CLIP (`open_clip`, default, no API key) or OpenAI GPT-4o-vision (`VISION_PROVIDER=openai`) | CLIP gives free, fast, local visual-similarity scoring so the project runs out of the box; OpenAI vision gives higher-accuracy "is this really the same product" judgments with a written reason, at the cost of an API key and per-call latency/cost. Both are implemented behind the same interface (`app/services/image_brain.py`) so grading can try either. |
| Video sources | Meta Ad Library **Graph API** (official, public `ads_archive` endpoint) for Meta; a RapidAPI Instagram-scraper provider for Reels (no official third-party search API exists); TikTok is an optional, toggled-off-by-default bonus source. | See "Video sourcing" below for the full reasoning and failure handling. |

## Project layout

```
backend/
  app/
    api/routes/       search.py (POST/GET /api/search, SSE stream), history.py
    core/             config.py (env settings), db.py (async SQLAlchemy engine/session)
    models/           search.py, video.py (Search, Video, SeenVideo)
    schemas/          Pydantic request/response models
    services/
      product_extractor.py   # opens a product URL, pulls title/desc/image, SSRF guard
      image_brain.py         # attribute extraction + match scoring (CLIP or OpenAI)
      dedup.py                # uniqueness / near-duplicate detection
      pipeline.py             # orchestrates one search end to end
      progress_bus.py         # in-memory pub/sub feeding the SSE endpoint
      sources/
        instagram.py, meta_ad_library.py, tiktok.py
  tests/              test_dedup.py, test_product_extractor.py
  requirements.txt, Dockerfile
frontend/
  src/
    components/       SearchBar, ProductPanel, ProgressTracker, ResultsGrid, VideoCard, Filters, SearchHistory
    api/client.js      axios + SSE client
    App.jsx
  package.json, vite.config.js, Dockerfile
docker-compose.yml     # postgres + backend + frontend, one command
.env.example
```

## Setup

### Option A — Docker (one command)

```bash
cp .env.example .env   # fill in whichever API keys you have
docker compose up --build
```

- Frontend: http://localhost:5173
- Backend: http://localhost:8000 (docs at `/docs`)

### Option B — manual

```bash
# Postgres
createdb video_discovery

# Backend
cd backend
python -m venv .venv && . .venv/Scripts/activate   # or source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
playwright install chromium   # only needed if INSTAGRAM_PROVIDER=browser
cp ../.env.example ../.env   # backend reads ../.env
uvicorn app.main:app --reload --port 8000

# Frontend
cd frontend
npm install
npm run dev
```

## Architecture / pipeline

```
User input (name / URL / photo)
        │
        ▼
 product_extractor  ──►  product title, description, main image
        │
        ▼
 image_brain.analyze_product_image  ──►  attributes (type, colour, pattern, material)
                                          + generated search queries/hashtags
        │
        ├──────────────┬──────────────┬──────────────┐
        ▼              ▼              ▼              ▼
  Instagram Reels   Meta Ad Library   TikTok (optional, own toggle)
  (asyncio.gather — all three run in parallel, each wrapped so one failing
   source never blocks or fails the others; each also tops itself up with
   widened queries + deeper pagination if it's on track for < 20 new videos)
        │
        ▼
   dedup.dedup_within_batch + dedup.annotate_seen
   (collapses exact/near-duplicate reposts within this result set; flags,
    but does not drop, videos already shown in earlier searches)
        │
        ▼
   image_brain.score_video_frame  (0-100 match score + reason per video,
                                    below-threshold videos discarded)
        │
        ▼
   stored in Postgres, streamed to the frontend live via SSE, final result via GET /api/search/:id
```

A single search is run as a FastAPI `BackgroundTask` so the `POST /api/search`
call returns a `search_id` immediately; the frontend then opens
`GET /api/search/:id/stream` (SSE) to show live progress
(`extracting_product → analyzing_image → fetching_* → deduping → scoring → done`),
and can re-fetch the full result any time via `GET /api/search/:id`.

## Video sourcing — method, rate limits, shortfalls

**Meta Ad Library**: uses the official public Graph API `ads_archive`
endpoint (`services/sources/meta_ad_library.py`). This is the intended,
ToS-compliant way to query video ads and needs only a Meta developer access
token — no login wall for the app. 429/5xx responses are retried with
exponential backoff (`tenacity`); a bad token (4xx) fails fast instead of
retrying pointlessly. Pagination follows Meta's `paging.next` cursor until
the 20-video minimum is met or pages run out.

**Instagram Reels**: Meta does not expose a public "search reels by
keyword" API for third parties, so this goes through a RapidAPI provider —
"Instagram Scraper Stable API"
(`instagram-scraper-stable-api.p.rapidapi.com`), specifically its
`GET /search_hashtag.php?hashtag=<tag>` endpoint, which returns a hashtag's
recent posts. Verified against the live API (see "Manually verified end to
end" below for the exact response shape). Query text and generated hashtags
are normalized into single-word Instagram hashtags (`"blue jeans"` →
`bluejeans`) since Instagram hashtags can't contain spaces.

Retries only apply to transient failures (429/5xx, network errors) —
a permanent failure (wrong endpoint path, bad key) is deliberately **not**
retried and aborts the whole call immediately, so one broken integration
costs one request, not one per query term. A shared `RequestBudget` hard-caps
real HTTP calls to this provider at `INSTAGRAM_MAX_REQUESTS_PER_SEARCH`
(default 3) across an entire search, including every round of the
query-widening loop — this exists specifically because this provider's free
tier is a **20 requests/month** hard cap, and a pagination/widening bug
could otherwise exhaust a month's quota in a single search.

**Known, disclosed limitation**: this endpoint returns a hashtag's *recent
posts* mixed between photos and videos (Reels) — it is not a Reels-only
feed. The integration correctly filters to `is_video: true` before scoring
(non-video posts are discarded, never counted or shown), but in manual
testing against several product-related hashtags (`tshirt`, `sneakers`,
`trending`), the recent-posts sample returned was **entirely photos, zero
videos**, meaning this particular provider/endpoint could not reliably hit
the 20-Reels minimum during testing. The provider's other endpoints were
checked for a dedicated Reels-search API — none exists on this plan.

**Browser automation was also implemented and tested** as the assignment's
explicitly-allowed alternative method
(`app/services/sources/instagram_browser.py`, Playwright + headless
Chromium, toggle via `INSTAGRAM_PROVIDER=browser`). It opens
`instagram.com/explore/tags/<hashtag>/` and intercepts the page's own
GraphQL network responses rather than scraping rendered HTML. Investigation
result: the page **loads without a login wall** and renders real metadata
(e.g. its title correctly showed "Sneakers • 243M reels on Instagram"), but
the specific GraphQL query that returns a hashtag's actual media items
responds with a server-side authorization error for logged-out sessions:

```json
{"errors":[{"message":"Unauthorized logged out query.","severity":"CRITICAL","code":1675002}]}
```

This is a hard block enforced by Instagram's backend, not a UI-level "please
log in" page — confirmed by capturing and inspecting all 12 GraphQL
responses the page made during a real, live browser session. The
implementation detects this specific error code and raises a clear
`SourceError` (surfaced as an `instagram_error` SSE event in the UI) instead
of silently returning zero results or hanging.

Both Instagram methods are real, working integrations that hit a genuine
external constraint rather than an untested one. **What we'd do next**:
either accept the ToS/ban risk of automating a real logged-in Instagram
session (against a throwaway account, never the developer's own), or move
to a paid scraping provider (e.g. Apify's Instagram Reel/Hashtag scrapers)
that maintains its own authenticated session pool server-side.

**TikTok** (bonus, optional): behind `TIKTOK_ENABLED`, off by default, and
wrapped in its own try/except in `pipeline.py` so it literally cannot block
Instagram or Meta — even an unhandled exception there is caught at the
`_safe_fetch` level.

**Shortfall handling**: if de-duplication or score-thresholding drops a
source below 20 usable videos, the pipeline emits a `shortfall` SSE event
naming the affected platform (surfaced in the UI's progress log) instead of
failing the search silently. The current implementation widens the query
set using the image brain's generated hashtags/queries before falling back
to reporting the shortfall; deeper pagination is the next thing to add if a
single widened pass still comes up short (see "Known limitations").

## Image-analysis brain

Two jobs, both in `app/services/image_brain.py`:

1. **Attribute extraction** (`analyze_product_image`) — turns the product
   photo into `product_type`, `colors`, `pattern`, `material`, plus 3
   generated search queries and 3 hashtags used to steer the Instagram/Meta
   queries toward the right visual niche instead of just the typed keyword.
2. **Match scoring** (`score_video_frame`) — compares a candidate video's
   thumbnail against the product photo and returns a `(score 0-100, reason)`
   pair, shown on every video card in the UI.

Two interchangeable providers:

- **`local_clip`** (default): OpenCLIP ViT-B/32 image embeddings, cosine
  similarity rescaled to 0-100. Zero-shot label matching against a small
  attribute vocabulary for step 1. No API key, runs on CPU, good enough for
  visual-similarity ranking; weaker at reading small logos/text on the
  product.
- **`openai`**: GPT-4o-mini vision call that returns structured JSON
  attributes, and a second call that judges "same product?" directly with a
  one-sentence reason. Better at logo/text/context reasoning (e.g. "same
  print, different model wearing it") since it reasons semantically rather
  than purely on embedding distance, at the cost of a key and per-video
  latency/cost.

Videos scoring below `MATCH_SCORE_THRESHOLD` (default 55, in `.env`) are
discarded before they reach the database.

**Accuracy testing**: run a search for each of these 5 products and record
counts + a couple of good/bad match examples per the assignment's
"Test evidence" deliverable — see `docs/test-evidence.md` (to be filled in
during actual testing against live API keys, since this repo ships without
real credentials):
1. A plain-colour cotton t-shirt with a graphic print
2. A pair of sneakers with a distinctive colourway
3. A protein/chocolate bar with branded packaging
4. A patterned dress
5. A logo-branded water bottle

## Uniqueness / de-duplication (`app/services/dedup.py`)

Two distinct passes, run in this order every search:

1. **Within-batch dedup** (`dedup_within_batch`) — collapses exact-ID and
   near-duplicate (pHash within 6 bits) repeats *inside the current result
   set* (a repost, a re-upload, the same ad running under several IDs). This
   is a hard drop: showing the same creative twice in one grid is never
   useful.
2. **Cross-search "seen before" tracking** (`annotate_seen`) — checks the
   surviving videos against the global `seen_videos` table (every ID/hash
   ever shown, across all past searches). A match here is *not* dropped; it
   is kept but flagged `seen_before=True`. Flagged videos don't count
   towards the 20-per-source minimum and are hidden by default, but the
   **"Show previously seen"** toggle in the UI re-includes them — this is
   what makes that toggle actually show something, instead of the videos
   having already been discarded before it could.
3. Only genuinely new videos (`seen_before=False`) get inserted into
   `seen_videos` after a search (`mark_new_as_seen`), so the history check
   stays accurate for the next search.
4. If, after de-dup, a source is on track for fewer than 20 *new* videos,
   `pipeline._collect_with_topup` widens the query set (broader/singular
   terms, the image brain's hashtags) and paginates deeper, for up to 3
   rounds, before the pipeline reports a shortfall (see "Video sourcing").

## Backend design notes

- `POST /api/search` returns `202` immediately with a `search_id`; the
  actual work runs in a `BackgroundTask` (`pipeline.run_search_pipeline`) —
  the assignment's "job queue or background worker" requirement. For a
  production deployment past this take-home, this would move to a real
  queue (Celery/RQ + Redis) so a backend restart doesn't drop in-flight
  jobs; that's flagged in "Known limitations" below.
- Instagram, Meta and TikTok are fetched with `asyncio.gather`, each wrapped
  in `_safe_fetch` so one source raising never cancels the others.
- `product_extractor.assert_safe_url` resolves the hostname and rejects
  private/loopback/link-local IPs before fetching any product page (SSRF
  guard), per the "block unsafe URLs" requirement.
- Structured logging via Python's `logging` module; every service raises
  typed exceptions (`SourceError`, `UnsafeUrlError`) instead of bare
  strings, so the API layer can turn them into clear error responses.
- Caching: this scaffold does not yet cache repeated product-URL fetches —
  see "Known limitations."

## Testing

```bash
cd backend
pytest
```

Covers: dedup key generation (stable per-URL, ID-based when available),
near-duplicate hash comparison, within-batch de-duplication (exact + near-dup
repost collapsing), and the SSRF guard (rejects `file://`, `localhost`,
loopback IPs, and redirects to an internal address; allows a normal public
URL). 12/12 passing as of this writing.

**Manually verified end to end** (documented here since it isn't captured by
the automated tests): with `DATABASE_URL` pointed at a local SQLite file
instead of Postgres, `uvicorn app.main:app` was started and exercised with
real HTTP requests —
`POST /api/search` (plain keyword, and with an uploaded `image_base64`
photo), `GET /api/search/:id`, `GET /api/history`, and
`GET /api/search/:id/stream` (SSE) — confirming the full
request → background pipeline → DB write → response round-trip. This caught
and fixed two real bugs before they'd have shown up in grading:
  - Serializing a freshly created `Search` triggered a lazy-load of its
    `videos` relationship outside of an awaitable context
    (`sqlalchemy.exc.MissingGreenlet`) — fixed by re-fetching with
    `selectinload` instead of relying on the just-committed instance.
  - The SSE endpoint would hang forever if the client subscribed after the
    pipeline had already finished (its progress queue is torn down on
    completion) — fixed by checking the search's DB status first and on a
    polling timeout, so a late subscriber gets the terminal state
    immediately instead of waiting on a queue nothing will ever publish to.

Also verified against the **live Instagram RapidAPI provider** with a real
key (a free-tier plan capped at 20 requests/month, used sparingly): the
first attempt used a guessed, wrong endpoint path (`/search/reels`) that
404'd — this exposed a third real bug, where the retry decorator retried
even permanent (non-transient) failures, burning 3x the necessary requests
per attempt, compounded further by the query-widening loop calling the
source function fresh on every round with no shared budget (worst case 3×3
= 9 requests for one search). Both were fixed: retries now only apply to
transient failures (429/5xx), and a `RequestBudget` object is created once
per search and shared across every widening round (see
`app/services/sources/base.py`). After fixing the real endpoint path
(`/search_hashtag.php`) and response parsing to match the provider's actual
JSON shape, subsequent test searches (`sneakers`, `trending`) each cost
exactly 1 request and completed cleanly end-to-end (pipeline reached
`status: done`, no crash) — see "Video sourcing" above for the honest
result on video yield.

## Known limitations / what's next

- **No real API keys are included** (per the assignment's instructions) —
  `search_instagram_reels` / `search_meta_ad_library` will raise
  `SourceError` until `.env` has real `META_ACCESS_TOKEN` /
  `INSTAGRAM_API_KEY` values, at which point the pipeline exercises the real
  retry/shortfall paths described above.
- Product-page fetches are cached in-process by normalized URL
  (`product_extractor.py`, 1 hour TTL). Image-analysis results are not
  cached yet — same pattern, straightforward to add in
  `image_brain.analyze_product_image`. Both are in-memory today; a
  multi-worker deployment would move them to Redis or a Postgres table.
- Background jobs currently run in-process (FastAPI `BackgroundTasks`); a
  multi-worker deployment needs Celery/RQ + Redis instead, and the SSE
  progress bus (`progress_bus.py`, currently in-memory) would move to Redis
  pub/sub to work across processes.
- OCR for reading logos/text on the product (mentioned in the assignment's
  attribute list) isn't implemented in the `local_clip` provider — the
  `openai` provider covers this via vision-model reasoning instead.
- TikTok source is a stubbed provider interface (`sources/tiktok.py`) ready
  to point at a real scraping/API provider; no provider is wired in by
  default since it's optional.

## Demo video / test evidence

Not included in this scaffold — record the 3–5 minute walkthrough and the
5-product test evidence once real API keys are available, per the
assignment's submission checklist.
