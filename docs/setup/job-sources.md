# Job sources: keys and settings

Five sources work without any key: **Remotive**, **Remote OK**, **We Work Remotely**, **Tanitjobs**
and **Keejob**. Two official APIs need a free key and widen coverage: **Adzuna** (Europe, USA,
Canada) and **JSearch** (RapidAPI, worldwide incl. the Gulf). Without their keys they simply stay
disabled (the *Sources* panel of the *Offres* page says so).

Keys go in `.env` only (never in git). After editing `.env`, run `make restart` so the API and
the worker reload it.

## Adzuna (free)

1. Go to <https://developer.adzuna.com/> and click **Register**.
2. Confirm your e-mail, then open **Dashboard → API Access Details**: you get an **Application ID**
   and an **Application Key**.
3. In `.env`:

   ```dotenv
   ADZUNA_APP_ID=your-application-id
   ADZUNA_APP_KEY=your-application-key
   ADZUNA_COUNTRIES=fr,de,gb,nl,be   # Adzuna has no Tunisia / Gulf coverage
   ```

JobPilot makes at most `ADZUNA_DAILY_CAP` (100) requests a day: one per country and search term.

## JSearch on RapidAPI (free plan)

1. Create an account on <https://rapidapi.com/> (e-mail or GitHub).
2. Open the **JSearch** API page (search "JSearch" by letscrape on RapidAPI) and choose
   **Subscribe to test** → the **Basic** (free) plan, about 200 requests a month.
3. Copy the **X-RapidAPI-Key** shown in the code snippets ("Header Parameters").
4. In `.env`:

   ```dotenv
   RAPIDAPI_KEY=your-rapidapi-key
   ```

The free plan is small, so JobPilot caps JSearch at `JSEARCH_DAILY_CAP` (6) requests a day: two
search terms per run, two runs a day.

## Schedule and politeness

- Every source runs at `JOBS_FETCH_HOURS` (UTC; default `7,17` = 08:00 and 18:00 in Tunis), and
  whenever you click **Actualiser** (responses are cached for 3 hours, so a second click costs
  the sources nothing).
- Requests carry an identifiable user agent with this project's URL, respect each site's
  `robots.txt` (checked before every scraped page), and stay under per-source daily caps:
  Remotive 4 (their API asks for at most 4 calls a day), Remote OK 4, We Work Remotely 8,
  Tanitjobs 6, Keejob 12 pages, 6 seconds apart.
- Attribution: every posting links back to its source, which is named on the card and in the
  detail panel, as Remotive's and Remote OK's terms ask.
- LinkedIn and Indeed are never scraped. OptionCarriere is excluded: its `robots.txt` forbids job
  pages and its RSS feed.
