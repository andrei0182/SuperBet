# SuperBet

Repo-ul are două părți independente:

1. **`src/superbet/`** — model de probabilitate pentru fotbal (Dixon-Coles), blend cu piața,
   miză Kelly fracționat și backtest walk-forward. Descris mai jos.
2. **`superbet_scraper/` + scripturile din rădăcină** — scraperul Superbet.ro și raportul zilnic
   (neschimbate, documentate în secțiunea [Scraper Superbet.ro](#scraper-superbetro)).

---

## Modelul de fotbal (`superbet`)

### Instalare

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest            # toate testele trebuie să fie verzi
```

Python 3.11+. Dependențe: numpy, pandas, scipy, scikit-learn, openpyxl, typer (+ pytest pentru teste).

### Date

Descarcă manual CSV-urile de la [football-data.co.uk](https://www.football-data.co.uk/data.php)
(ex. `E0_2324.csv` = Premier League 2023/24, redenumit după preferință) și pune-le în `data/raw/`.
`data/` și `outputs/` sunt în `.gitignore` — nu se comit date brute. Nu se face scraping.

Coloane folosite:

| Rol | Coloane | Folosire |
|---|---|---|
| meci | `Div, Date, HomeTeam, AwayTeam, FTHG, FTAG` | model + rezultat |
| cote la momentul pariului (1X2) | `B365H, B365D, B365A` (fallback `AvgH, AvgD, AvgA`) | piața din blend + cota luată |
| cote la momentul pariului (O/U 2.5) | `B365>2.5, B365<2.5` (fallback `Avg>2.5, Avg<2.5`) | idem |
| cote de închidere Pinnacle | `PSCH, PSCD, PSCA`, `PC>2.5, PC<2.5` | **doar CLV** și baseline informativ, niciodată input în model sau blend |

`--odds-source Avg` inversează preferința (Avg întâi, B365 ca fallback).

**GG / BTTS**: football-data.co.uk nu are cote GG. Modelul calculează oricum P(GG), dar ROI-ul pe GG
se calculează doar dacă există `data/raw/btts_odds.csv` cu coloanele
`Date, HomeTeam, AwayTeam, gg_yes, gg_no` (se poate genera cu scraperul BetExplorer din
`andrei0182/bet`). Fără fișier, GG apare în raport doar cu log loss / Brier față de rezultatul real.

### Cum funcționează

- **Dixon-Coles** (`dixon_coles.py`): `λ = exp(a_home + d_away + h)`, `μ = exp(a_away + d_home)`,
  corecția `τ` pentru scoruri mici, ponderi `exp(−ξ · zile)`, maximum likelihood cu L-BFGS-B
  (gradient analitic), `Σ a = 0`, penalizare L2 mică pe `a` și `d` centrate. Un model per ligă (`Div`).
  Echipele fără istoric (promovate) primesc media ligii. Matricea de scoruri 0..10 × 0..10, renormalizată.
- **ξ** se alege din grila `[0, 0.0005, 0.001, 0.002, 0.003]` prin validare walk-forward (4 ferestre
  de 30 de zile, log loss 1X2 minim), folosind **doar date anterioare** momentului de refit;
  se re-selectează la fiecare 180 de zile. `--xi 0.002` fixează valoarea.
- **Piețe** (`markets.py`): 1X2, Over/Under pe orice linie (1.5 / 2.5 / 3.5…), GG/NG, handicap asiatic
  (linii întregi, jumătăți și sferturi).
- **De-vig** (`blend.py`): proporțional `p_i = (1/o_i) / Σ(1/o_j)` (implicit) sau metoda power
  (`--devig-method power`).
- **Blend**: regresie logistică pe `[logit(P_model), logit(P_piață)]`; la 1X2 cele trei rezultate sunt
  stivuite one-vs-rest (cu dummies pe rezultat) și apoi renormalizate. Blenderul se reantrenează la
  fiecare bloc doar pe meciuri deja terminate, pe predicții out-of-sample ale modelului.
- **Value & miză** (`staking.py`): `EV = P_final · cotă − 1`; pariu doar dacă `EV ≥ --ev-min` (0.03);
  maxim un pariu per meci și piață (rezultatul cu EV maxim).
  `f* = (P·cotă − 1)/(cotă − 1)`, `miză = --kelly (0.25) · f* · bankroll`, plafonată la `--cap` (2%).
  Mizele unei zile se calculează pe bankroll-ul de la începutul zilei.

### Backtest walk-forward (fără data leakage)

1. Pentru fiecare ligă, primul bloc începe după primele `--min-train` (300) meciuri.
2. La fiecare `--refit-days` (7) zile: modelul se antrenează pe meciurile cu dată `< t`,
   prezice meciurile din `[t, t + 7 zile)`, apoi se avansează.
3. Blenderul pentru blocul `[t, t+7)` e antrenat pe predicțiile out-of-sample ale meciurilor terminate `< t`
   (minim 200). Predicțiile de dinainte de `--start` servesc doar la încălzirea blenderului.
4. Pariurile și toate metricile se calculează doar pe meciurile cu dată `≥ --start`.
5. Cotele de închidere intră doar în CLV (`cota_luată / cota_închidere − 1`) și ca baseline informativ.

Piețe backtestate: 1X2 și Over/Under 2.5; GG doar cu `btts_odds.csv`.

Raportul (`outputs/summary.json` + consolă) conține: nr. pariuri, hit rate, yield (profit/mize),
ROI pe bankroll, max drawdown, CLV mediu (și % pariuri cu CLV pozitiv), log loss și Brier pentru
model / piață (cote la momentul pariului, de-vig) / blend pe **aceleași** meciuri, plus
baseline-ul Pinnacle closing. Dacă modelul nu bate piața la log loss, raportul spune explicit:
`ATENȚIE: modelul NU bate piața la log loss walk-forward -> nu are edge demonstrat`.

### Comenzi CLI

```bash
# antrenează modelele finale (per ligă) + blenderele; salvează totul într-un pickle
superbet fit --data data/raw --out outputs/model.pkl

# predicții pentru meciuri viitoare
# fixtures.csv: Date,HomeTeam,AwayTeam[,Div] + opțional B365H,B365D,B365A,B365>2.5,B365<2.5,gg_yes,gg_no
superbet predict --model outputs/model.pkl --fixtures fixtures.csv --bankroll 1000

# backtest walk-forward
superbet backtest --data data/raw --start 2022-08-01 --ev-min 0.03 --kelly 0.25
```

Opțiuni utile: `--cap 0.02`, `--bankroll 1000`, `--refit-days 30` (refit lunar, mai rapid),
`--min-train 300`, `--xi 0.002`, `--odds-source Avg`, `--devig-method power`, `--out-dir outputs`.
`superbet <comandă> --help` pentru lista completă.

Exemplu de rulare: pune `E0_2021.csv`, `E0_2122.csv`, `E0_2223.csv`, `E0_2324.csv` în `data/raw/`, apoi
`superbet backtest --data data/raw --start 2022-08-01`. Ieșiri în `outputs/`:

- `bets.csv` — toate pariurile (dată, meci, piață, selecție, P model / piață / final, cotă, cotă închidere, EV, miză, profit, bankroll);
- `predictions.csv` — toate predicțiile out-of-sample de după start;
- `summary.json` — configurația, ξ ales, coeficienții blend-ului și toate metricile.

### Limitări

- Dixon-Coles folosește doar scorurile: fără accidentări, rotații, motivație, vreme, xG.
- Echipele promovate pornesc de la media ligii (în realitate sunt de obicei sub medie) până acumulează meciuri.
- Ligile sunt modelate separat; nu se transferă forța între ligi.
- `B365`/`Avg` din football-data sunt cote colectate cu puțin înainte de meci; în realitate cota la care
  pariezi poate diferi. Backtest-ul presupune că pariul s-a plasat exact la acea cotă, fără limite de miză.
- CLV-ul e disponibil doar unde există cote Pinnacle closing în fișier.

### Note importante

- Un model nu garantează profit; majoritatea pariorilor pierd pe termen lung.
  Singurul semn credibil de edge e **CLV pozitiv constant pe un eșantion mare** (sute/mii de pariuri).
- Dacă modelul nu bate piața la log loss walk-forward, nu are edge, indiferent de ROI-ul pe perioade scurte.
- Mizele din backtest sunt teoretice; în realitate casele limitează conturile câștigătoare și cotele se mișcă.
- Pariază doar sume pe care îți permiți să le pierzi.

---

## Scraper Superbet.ro

**Status: Phase 0 — reconnaissance scaffold, not a working scraper yet.**

Goal (mirrors the BetExplorer project): scrape odds (1X2, Over/Under) and
team history/stats from superbet.ro, exported to a formatted Excel file.

### Why this starts as a scaffold, not finished code

We don't yet know Superbet.ro's actual data structure — whether pages are
server-rendered (plain `requests` sees everything, like BetExplorer's
league pages and AJAX odds endpoints turned out to be) or need real JS
execution (like BetExplorer's per-match standings widget, which was
unreliable via Selenium and only worked reliably once we found the
equivalent AJAX endpoint instead). Guessing wrong here wastes a lot of time
— today's BetExplorer project spent real effort on exactly this mistake
more than once. So: investigate first, then build.

### Setup

```bash
pip install -r requirements.txt
```

### Step 1 — investigate (do this first)

```bash
python tools/inspect_page.py "https://superbet.ro/pariuri-sportive/fotbal/italia/serie-a/toate"
```

This checks the same URL two ways — plain HTTP request, and a real
(headless) browser — and tells you whether match/odds content is present
in the raw HTML or only appears after JS runs. It also saves both versions
to `/tmp/` for manual inspection (`grep`, or download and open in a
browser).

**Also check manually, in your own browser's DevTools Network tab**, while
browsing a Superbet.ro odds page: filter for `Fetch/XHR` requests. If
there's a JSON API behind the page (very likely for a modern betting site),
that's usually the fastest and most reliable data source — skip both DOM
scraping approaches entirely and hit that endpoint directly with
`requests`, the same pattern that ended up working best for BetExplorer's
odds and team-stats data today.

### Once the data source is confirmed

Update this README and `superbet_scraper/models.py`'s `Match` fields to
match reality, then build out the actual scraping logic — `driver.py` is
already in place if Selenium turns out to be needed for any step, and
`requirements.txt` already includes `requests` for the AJAX/JSON path if
that's what we find instead.

### Project layout

- `superbet_scraper/driver.py` — headless Chrome builder (only needed if a
  step genuinely requires JS execution — confirm this before relying on it).
- `superbet_scraper/models.py` — data model, currently a placeholder mirroring
  the BetExplorer project's shape.
- `tools/inspect_page.py` — **run this first**, on any Superbet.ro page you
  want to scrape, before writing extraction logic for it.
- `output/` — where exported Excel files will go (gitignored).
