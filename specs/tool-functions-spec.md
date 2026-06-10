# Spec: Tool Functions

**File:** `tools.py`
**Status:** `get_seasonal_conditions` — Pre-implemented, read through. `lookup_plant` — complete spec fields before implementing.

---

## Purpose

These two functions are the tools the agent can call. They retrieve structured data from the local plant database and seasonal data files and return it to the agent loop, which passes it to the LLM as context for generating a response.

---

## Function 1: `lookup_plant()`

### Input / Output Contract

**Inputs:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `plant_name` | `str` | The plant name as entered by the user or chosen by the LLM — may be any casing, common name, scientific name, or alias |

**Output:** `dict`

When the plant is **found**, return:
```python
{"found": True, "plant": <the full plant dict from _plant_db>}
```

When the plant is **not found**, return:
```python
{"found": False, "name": <normalized input>, "message": <helpful string>}
```

---

### Design Decisions

*Complete the two blank fields below before writing code. The others are pre-filled for you.*

---

#### Input normalization

Strip leading/trailing whitespace and convert to lowercase before any comparison.

```python
normalized = plant_name.strip().lower()
```

---

#### Search order

Search in this order: direct key → display name → aliases. Keys are the fastest
lookup (O(1) dict access), so check those first. Display names are the next most
likely match for clean user input. Aliases are the broadest net, so they go last.

```
1. Direct key match: normalized in _plant_db
2. Display name match: plant["display_name"].lower() == normalized
3. Alias match: normalized in [alias.lower() for alias in plant["aliases"]]
```

---

#### Alias matching approach

For each plant, do a case-insensitive membership test of the normalized input
against its `aliases` list. Lowercase every alias at compare time and test for
exact equality, so `"Sansevieria"`, `"sansevieria"`, and `" sansevieria "`
(already stripped by normalization) all match the same slug:

```
match = any(normalized == alias.lower() for alias in plant["aliases"])
```

**Why exact equality, not substring:** substring matching (`normalized in alias`)
would cause false positives — e.g. `"aloe"` is a substring of nothing harmful here,
but `"fig"` would match both "fiddle fig" and "banjo fig" unpredictably, and short
inputs like `"ivy"` could match the wrong plant. Exact equality on a fully
normalized string is the reliable choice for a curated database.

**Scaling note:** this is an O(plants × aliases) linear scan. At ~15 plants that is
fine. If the database grew to thousands of plants, I'd precompute a single reverse
index `dict` once at module load — mapping every key, lowercased display name, and
lowercased alias to its slug — turning each lookup into one O(1) dict access instead
of a full scan. I'm not doing that now because it adds indexing complexity the
current scale doesn't justify.

---

#### Not-found message

The agent (LLM) reads this string and decides what to tell the user, so the message
has to do two jobs: (1) state plainly that the plant is not in the curated database,
and (2) give the agent the data it needs to respond well — namely the list of plants
that *are* covered, so it can suggest a close match or pivot. It also tells the agent
it may still offer general care guidance, while being honest that the advice is
general rather than database-backed (so the agent doesn't fabricate database-specific
detail). The exact string, built dynamically so it stays in sync with the database:

```
f"No plant matching '{normalized}' was found in the plant database. The database "
f"currently covers: {available}. If the user's plant is one of these under a "
f"different name, use that entry; otherwise tell the user this specific plant "
f"isn't in the curated database and offer general houseplant care guidance, making "
f"clear the advice is general rather than from the database."
```

where `available` is the comma-joined `display_name` of every plant in `_plant_db`.

---

#### Implementation Notes

*Fill this in after implementing and running the app.*

**Test: does `"devil's ivy"` return the pothos entry?**
```
Yes — {"found": True, "plant": {...full Pothos dict...}}. Matched via the alias
branch (3rd in search order), since "devil's ivy" is in pothos["aliases"].
```

**Test: does `"SNAKE PLANT"` return the snake plant entry?**
```
Yes — returns the Snake Plant dict. Normalization lowercases "SNAKE PLANT" to
"snake plant", which matches plant["display_name"].lower() in the display-name branch.
```

**One edge case you discovered while implementing:**
```
The not-found contract returns "name": <normalized input>, but the original docstring
said <original input>. I followed the spec contract and returned the normalized
string, so the agent and any logs see the same value matching was actually done on.
Also confirmed whitespace handling: "  pothos  " matches because .strip() runs before
the O(1) key lookup — without the strip, the leading/trailing spaces would miss the
exact key match entirely.
```

---

## Function 2: `get_seasonal_conditions()`

### Input / Output Contract

**Inputs:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `season` | `str \| None` | One of `"spring"`, `"summer"`, `"fall"`, `"winter"`, or `None` to auto-detect |

**Output:** `dict`

The full season dict from `_season_data`, plus one additional field:

| Added field | Type | Value |
|-------------|------|-------|
| `"detected_season"` | `bool` | `True` if auto-detected from the month; `False` if season was passed as an argument |

---

### Design Decisions

*This function is pre-implemented — read through these fields and the code before working on `lookup_plant`.*

---

#### Auto-detection logic

When `season` is `None`, get the current calendar month with `datetime.now().month`
and look it up in the `_MONTH_TO_SEASON` dict, which maps month numbers to season strings.

```python
current_month = datetime.now().month
season_key = _MONTH_TO_SEASON[current_month]
```

---

#### Season validation

If the caller passes an invalid season string (e.g., `"monsoon"`), the function
falls back to auto-detection — same as if `None` were passed. The `VALID_SEASONS`
set acts as the gate:

```python
VALID_SEASONS = {"spring", "summer", "fall", "winter"}
if season and season.lower() in VALID_SEASONS:
    ...  # use provided season
else:
    ...  # auto-detect
```

---

#### Return structure

The full season dict from `_season_data`, plus a `detected_season` boolean. Example for spring:

```python
{
    "season": "spring",
    "watering": "Increase watering frequency as plants break dormancy ...",
    "fertilizing": "Resume feeding with a balanced fertilizer ...",
    "light": "Days are lengthening — move plants closer to windows ...",
    "pests": "Watch for spider mites and aphids as temperatures rise ...",
    "detected_season": True   # True = auto-detected; False = caller specified
}
```

---

#### Implementation Notes

*Fill this in after testing.*

**Test: does calling with `season=None` return the correct season for the current month?**
```
Current month: June (month 6)
Expected season: summer (_MONTH_TO_SEASON[6] == "summer")
Returned season: Summer — detected_season: True
```

**Test: does calling with `season="winter"` return winter data regardless of the current month?**
```
Yes — returns the Winter dict with detected_season: False (caller-specified, not
auto-detected). Also verified that an invalid season like "monsoon" falls through to
auto-detection (detected_season: True), as the VALID_SEASONS gate intends.
```
