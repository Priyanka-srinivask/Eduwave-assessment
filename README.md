# 🌊 Wavy Tutor

A grounded, safe, and adaptive **Socratic AI tutor for Grade-5 fractions**.

A learner sends a message; the system retrieves relevant curriculum context,
reasons over the conversation so far, and returns **age-appropriate guidance in a
validated JSON structure** — guiding the learner toward understanding instead of
just handing over the answer.

Built for the Eduwave AI Engineering Intern take-home challenge.

---

## Table of contents
- [Quick start](#quick-start)
- [Using the real Gemini model](#using-the-real-gemini-model-optional)
- [API surface](#api-surface)
- [Architecture overview](#architecture-overview)
- [Testing & evaluation](#testing--evaluation)
- [README questions (answered)](#readme-questions-answered)
- [Known limitations & next three improvements](#known-limitations--next-three-improvements)
- [Project layout](#project-layout)

---

## Quick start

**Requirements:** Python 3.10+ (developed on 3.14). No API key needed — the app
runs offline out of the box with a deterministic mock model.

```bash
# 1. (optional) create a virtual environment
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate

# 2. install dependencies
pip install -r requirements.txt

# 3. run the server
python -m uvicorn app.main:app --reload --port 8000
```

Then open **http://localhost:8000** for the chat UI, or hit the API directly.

Run the tests and the evaluation suite:

```bash
python -m pytest              # 31 unit/integration tests
python -m eval.run_eval       # 12 behavioral evaluation cases
```

### Exercise the full flow with curl

```bash
# create a session
curl -s -X POST http://localhost:8000/api/sessions \
  -H "Content-Type: application/json" -d '{"language":"en"}'
# -> {"id":"<SESSION_ID>", ...}

# send a message (use the id from above)
curl -s -X POST http://localhost:8000/api/sessions/<SESSION_ID>/messages \
  -H "Content-Type: application/json" -d '{"message":"Is 2/5 bigger than 1/2?"}'

# fetch the whole session (history + telemetry)
curl -s http://localhost:8000/api/sessions/<SESSION_ID>

# health
curl -s http://localhost:8000/health
```

---

## Using the real Gemini model (optional)

The app defaults to an offline mock so it is instantly runnable. To use Google
Gemini's **free tier**:

1. Get a free API key at <https://aistudio.google.com/app/apikey>.
2. `cp .env.example .env`
3. Edit `.env`:
   ```env
   LLM_PROVIDER=gemini
   GEMINI_API_KEY=your_real_key_here
   ```
4. Restart the server. `GET /health` will report `"provider":"gemini"`.

The key is read from the environment only and is **never committed** (`.env` is
git-ignored). Tests and the eval suite always force the mock, so they never need
a key or network.

---

## API surface

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/api/sessions` | Create a tutoring session. Body: `{ "learner_name?": str, "language?": "en"\|"es" }` → `201` |
| `POST` | `/api/sessions/{id}/messages` | Submit a learner message; returns a validated tutor response + telemetry. |
| `GET`  | `/api/sessions/{id}` | Fetch a session with full message history. |
| `GET`  | `/health` | Liveness + active provider/model (no secrets). |

**Response contract** (from `POST .../messages` → `response`):

```json
{
  "tutorMessage": "Let's compare the fractions using a common denominator.",
  "misconception": "numerator_only_comparison",
  "nextQuestion": "What denominator could both 3 and 4 share?",
  "confidence": 0.84,
  "curriculumCitations": ["fractions.compare.02"],
  "safetyFlags": []
}
```

Interactive OpenAPI docs are available at **http://localhost:8000/docs**.

---

## Architecture overview

The system is a thin FastAPI HTTP layer over a set of single-responsibility
modules. The orchestrator (`app/tutor.py`) is the spine that ties them together.

```
                        ┌─────────────────────────────────────────────┐
   HTTP request         │                app/main.py                  │
  ───────────────────▶  │   (FastAPI: validate input, map errors)     │
                        └───────────────────────┬─────────────────────┘
                                                │ handle_message()
                                                ▼
                        ┌─────────────────────────────────────────────┐
                        │              app/tutor.py                    │
                        │            (orchestrator)                    │
                        └──┬───────┬────────┬────────┬────────┬────────┘
             1. safety     │       │2.memory│3.retr. │4.prompt│5.LLM  6.validate+ground  7.persist
                           ▼       ▼        ▼        ▼        ▼
                     safety.py   db.py  retrieval  prompt.py llm/*  models.py  db.py
                                          .py                        + observability.py
```

**Request lifecycle** (see the detailed answer in
[README question 1](#1-walk-through-the-request-lifecycle-from-incoming-message-to-stored-response)).

**Design principles**
- **Single responsibility per module** → clear boundaries; any interview
  question maps to exactly one file.
- **The app depends on abstractions, not vendors** → the LLM lives behind a
  `Protocol`; swapping/adding providers or running offline is trivial.
- **Never trust the model** → structured validation + citation grounding + a
  safety pre-filter, so a bad or adversarial model reply can't corrupt output.
- **Failures degrade, they don't crash** → every failure path returns a
  controlled response or a clean HTTP error.

---

## Testing & evaluation

**Unit/integration tests** (`tests/`, 31 tests) — cover the five scenarios the
brief requires plus edge cases:

| Scenario (from brief) | Where |
|---|---|
| Retrieval returns relevant IDs | `tests/test_retrieval.py` |
| Validation rejects/repairs malformed output | `tests/test_validation.py` |
| Follow-up uses prior context | `tests/test_memory.py` |
| Prompt injection doesn't override behavior | `tests/test_safety.py` |
| Controlled response on provider failure | `tests/test_reliability.py` |

**Behavioral evaluation** (`eval/eval_cases.json`, 12 cases) — declarative cases
with `input`, `expect` (machine-checkable), and `judged_by` (human-readable).
Run `python -m eval.run_eval` (add `--report eval/report.md` for a Markdown
report). Every case runs against the real orchestrator with the deterministic
mock, so it needs no key and gives the same result every time.

Both suites run **fully offline**.

---

## README questions (answered)

### 1. Walk through the request lifecycle from incoming message to stored response.

1. **HTTP in** — `POST /api/sessions/{id}/messages`. FastAPI validates the body
   against `PostMessageRequest` (non-empty, ≤2000 chars); invalid input is
   rejected with `422` before any work happens.
2. **Session check** — the orchestrator loads the session; unknown → `404`.
3. **Idempotency check** (if a `client_message_id` is supplied) — a previously
   answered duplicate replays the stored response instead of calling the model.
4. **Persist learner turn** — written first, so it's never lost and becomes part
   of history even if a later step fails.
5. **Safety pre-filter** — deterministic screening for injection, prompt-probing,
   age-inappropriate content and profanity. Hard-block categories return a safe
   canned reply and **skip the model entirely**.
6. **Load memory** — recent messages for this session.
7. **Retrieve** — score the curriculum and select the top-K relevant items; the
   set of retrieved IDs becomes the *allow-list* for citations.
8. **Build prompt** — system rules + retrieved context + recent history + the new
   message.
9. **Call the model** — through the provider abstraction, with a hard timeout and
   bounded exponential-backoff retries.
10. **Validate + ground** — parse JSON (with repair for fenced/chatty output),
    validate against `TutorResponse`, then **strip any citation not in the
    retrieved allow-list**. Any failure here degrades to a controlled fallback.
11. **Persist + observe** — store the tutor turn with telemetry (latency,
    provider/model, tokens, estimated cost, retrieval IDs, error) and log a
    non-sensitive summary.
12. **HTTP out** — return `{ response, telemetry, session_id }` with `200`.

### 2. How does retrieval work, and what would make it fail?

Retrieval (`app/retrieval.py`) is a **transparent keyword/hybrid scorer** over
the 8-item curriculum. For each item it sums weighted signals: curated keyword
matches (×3), title matches (×2), misconception matches (×2), content matches
(×1), and a shared-explicit-fraction signal (×2). It returns the top-K items with
a **non-zero** score, each annotated with the terms/reasons that matched (so the
API can *show why* context was chosen). Zero-scoring items are excluded, so an
off-topic message yields no grounding.

**Why keyword, not embeddings?** For a tiny fixed corpus, a lexical scorer is
fully inspectable — I can explain exactly why any item ranked where it did, which
is worth more here than an opaque similarity number. The brief explicitly allows
this.

**What would make it fail:** paraphrases with no shared vocabulary (e.g. "which
is heftier, two-fifths or one-half?" — no digits, no "fraction"), synonyms the
curated keywords don't cover, or spelling errors. **Mitigation / upgrade path:**
add embeddings (e.g. `text-embedding-004`) for semantic recall and keep the
keyword score as a re-ranker — a hybrid that preserves explainability while
covering paraphrase. The interface (`retrieve()`) wouldn't change.

### 3. What does your confidence score mean? Is it calibrated or only heuristic?

It is **heuristic, not calibrated**. Two sources:
- On a real model turn, `confidence` is the model's self-reported confidence in
  its *diagnosis of the learner's misconception* — a soft signal, not a
  probability.
- On any fallback (provider failure, unparseable/invalid output), we hard-set
  `confidence = 0.0`. So **0.0 reliably means "this was not a model-derived
  answer,"** which is the one guarantee the score does make.

To make it *calibrated* I'd need labelled outcomes (did the hint actually help?)
and post-hoc calibration (e.g. isotonic/Platt scaling) against those labels —
out of scope here, and I'd rather ship an honest heuristic than a falsely precise
number.

### 4. How do you prevent the model from inventing curriculum citations?

Two layers:
1. **Prompt** — the model is told it may cite *only* the IDs supplied in the
   context and must never invent one.
2. **Code enforcement** (the guarantee) — in `app/tutor.py`, `_ground_citations()`
   intersects the model's citations with the set of IDs we *actually retrieved
   and supplied*. Anything else is dropped before the response is returned or
   stored. Because the check runs in code against a known allow-list, a
   hallucinated ID can never reach the user — regardless of what the model says.
   This is verified by `tests/test_validation.py::test_grounding_strips_invented_citations`.

### 5. What happens if the provider times out or returns invalid JSON?

Every failure mode returns a **controlled** response (HTTP `200` with a graceful
fallback), never a crash:
- **Timeout / rate-limit / provider error** → bounded-backoff retries; if all
  fail, a safe fallback message with `confidence: 0.0` and
  `error: "provider_error: ProviderTimeout"` in telemetry.
- **Malformed (non-JSON) output** → 3-stage repair (strict parse → strip code
  fences → extract first `{...}`); if still unparseable → fallback,
  `error: "malformed_json"`.
- **Valid JSON but wrong shape** (e.g. `confidence: 5`) → Pydantic rejects it →
  fallback, `error: "schema_invalid"`.
- **Database failure** → wrapped as `DatabaseError` → clean `503`, and a persist
  failure never blocks the user's reply.

All of these are covered by `tests/test_reliability.py`.

### 6. What would you change for 100,000 learner messages per day?

~1.2 requests/sec average (with peaks much higher). Changes, in priority order:
- **Database:** move from SQLite to **PostgreSQL** (SQLite is single-writer;
  fine for this assessment, not for concurrent write load). The `db.py` boundary
  keeps this a localized change.
- **Concurrency:** the current per-request `time.sleep` backoff and threadpool
  timeout block a worker. Make the LLM path **fully async** (async HTTP client)
  so a worker isn't tied up while waiting on the model.
- **Caching:** cache retrieval results and cache/deduplicate identical prompts;
  reuse embeddings if adopted.
- **Cost & latency:** batch telemetry writes; move telemetry to a separate store
  or a queue so logging never sits on the request path.
- **Memory at scale:** summarize old turns (see below) to bound prompt size.
- **Ops:** horizontal scaling behind a load balancer, per-learner rate limiting,
  autoscaling on the model-call queue, dashboards on the telemetry we already
  record.

**Very long conversations:** the prompt currently includes the last N turns
(`history_window`). Beyond that I'd keep a **rolling summary** of older turns
(a "learner profile": known misconceptions, mastered concepts) plus the last few
verbatim turns — bounding tokens while preserving continuity.

### 7. How would you reduce model cost by 50% without materially harming learning quality?

- **Right-size the model:** Gemini 1.5 **Flash** (already chosen) over Pro —
  most fraction tutoring turns don't need the larger model. Route only genuinely
  hard turns to a bigger model (a cheap classifier decides).
- **Shrink the prompt:** send only top-K retrieved items (already done) and
  summarized history rather than full transcripts — input tokens dominate cost.
- **Cache & dedupe:** many learners ask near-identical starter questions; cache
  responses for common (question, context) pairs and short-circuit duplicates
  via the existing idempotency path.
- **Cap output tokens** and keep responses concise (good pedagogy anyway).
- **Skip the model entirely** when the safety pre-filter hard-blocks (already
  done) — those turns cost \$0.

The observability we record (per-turn tokens + estimated cost) makes it possible
to *measure* the savings rather than guess.

### 8. What is the biggest weakness in your current implementation?

**Retrieval recall on paraphrased questions.** The keyword scorer is precise and
explainable but will miss a learner who asks a conceptually-relevant question
using none of the curated vocabulary — returning no grounding and a weaker,
generic reply. It's the highest-impact place to improve, and the fix (a hybrid
embedding + keyword re-ranker) is well understood and wouldn't change the module
interface. A close second: safety relies partly on English regex patterns, which
are inherently incomplete against novel phrasings (mitigated, not solved, by the
prompt-level and validation layers).

---

## Known limitations & next three improvements

**Known limitations**
- Keyword retrieval misses vocabulary-free paraphrases (see Q8).
- Confidence is heuristic, not calibrated (see Q3).
- Safety patterns are English-only and illustrative, not exhaustive.
- SQLite suits a single node; not built for high concurrent write load.
- The mock provider's phrasing is intentionally simple (deterministic for tests);
  real pedagogical quality depends on the live model.

**Next three improvements (in priority order)**
1. **Hybrid retrieval** — add embeddings as a semantic recall layer, keep the
   keyword score as an explainable re-ranker.
2. **Rolling conversation summary** — bound prompt size on long sessions while
   preserving a per-learner misconception/mastery profile.
3. **Provider fallback chain** — the abstraction is ready; add a second provider
   with automatic failover so a single provider outage doesn't degrade service.

---

## Project layout

```
app/
  main.py          # FastAPI app + the 4 routes (thin HTTP layer)
  config.py        # typed settings from env / .env (no secrets in code)
  models.py        # Pydantic schemas — the response contract
  db.py            # SQLite persistence (sessions, messages, telemetry)
  retrieval.py     # keyword/hybrid curriculum retrieval (grounding)
  safety.py        # input guardrails + prompt-injection resistance
  prompt.py        # system prompt + per-turn prompt assembly
  tutor.py         # orchestrator — the request lifecycle in code
  observability.py # cost estimation + safe structured logging
  llm/
    base.py        # provider Protocol + result/error types
    mock.py        # deterministic offline provider (tests/CI)
    gemini.py      # real Google Gemini provider
data/curriculum.json  # the supplied curriculum (IDs preserved)
web/index.html        # minimal self-contained chat UI
tests/                # 31 unit/integration tests (offline)
eval/                 # 12 behavioral eval cases + runner + report
```

See [`AI_USAGE.md`](AI_USAGE.md) for how AI tools were used and verified.
