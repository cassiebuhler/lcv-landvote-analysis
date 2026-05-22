"""
Scrape LCV vote descriptions and classify by policy mechanism.


Categories
----------
1. funding_appropriations         — dedicated $$ for conservation programs
                                    (LandVote-analogous bucket)
2. regulation_standards           — rules, restrictions, permits, standards,
                                    agency rulemaking, species protection rules
3. land_designation_management    — wilderness/monument designation, leasing
                                    decisions for specific lands, land transfers
4. other                          — confirmations, procedural, sense-of-Congress

Each vote also gets a 'classification_confidence' flag (high / low) based on
how many rule categories fired and how decisively. Low-confidence rows should
be reviewed manually before reporting numbers.

Usage
-----
    pip install pandas requests beautifulsoup4 lxml
    python classify_lcv_votes_keyword.py \
        --input  lcv_congressional_votes_filtered.csv \
        --output lcv_votes_classified.csv \
        --cache  lcv_descriptions_cache.csv

The --cache file stores scraped descriptions so re-runs are fast. Delete
the cache file to re-scrape from scratch.

Failed scrapes are recorded with mechanism_category='SCRAPE_FAILED' and
listed at the end of the run for manual handling.
"""

import argparse
import re
import sys
import time
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (research scraper; contact: researcher@example.edu)"
}
REQUEST_DELAY = 1.0  # seconds between requests, be polite
TIMEOUT = 20


# ---------------------------------------------------------------------------
# 1. Scraping
# ---------------------------------------------------------------------------

def scrape_description(url: str) -> str | None:
    """Fetch an LCV vote page and extract the description text.

    LCV vote pages have the structure:
        <h1>Vote Title</h1>
        ... vote counts, share buttons ...
        Issues: [tag1] [tag2]
        <body paragraphs with the description>
        ### Votes
        ... member-by-member vote list ...

    The description paragraphs sit between the Issues line and the
    "Votes" / "Show" section. We extract them by grabbing the page's
    main content area and pulling paragraph tags until we hit the votes
    block.

    Returns None on failure.
    """
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"  REQUEST ERROR: {e}", file=sys.stderr)
        return None
    if r.status_code != 200:
        print(f"  HTTP {r.status_code}", file=sys.stderr)
        return None

    soup = BeautifulSoup(r.text, "lxml")

    # Strategy: og:description is reliably set by LCV's WP setup and contains
    # the opening of the description text. We can also pull all <p> tags from
    # the main content and concatenate them, then trim out the membership
    # state-by-state block that follows.
    og = soup.find("meta", property="og:description")
    og_desc = og["content"].strip() if og and og.get("content") else ""

    # Walk all <p> elements; the description is the cluster of paragraphs
    # that occur after the issues nav and before the per-state member list.
    # We use a simple heuristic: paragraphs longer than 80 chars that don't
    # match navigation or footer boilerplate.
    paragraphs = []
    for p in soup.find_all("p"):
        text = p.get_text(" ", strip=True)
        if len(text) < 80:
            continue
        # Filter boilerplate
        lowered = text.lower()
        if any(skip in lowered for skip in [
            "stay informed", "©", "lcv.org", "privacy policy",
            "congressional scorecard tracks",
        ]):
            continue
        paragraphs.append(text)

    description = "\n\n".join(paragraphs).strip()

    # If we got nothing from <p>, fall back to og_desc
    if not description and og_desc:
        description = og_desc

    return description or None


def scrape_all(df: pd.DataFrame, cache_path: Path) -> pd.DataFrame:
    """Scrape descriptions for every row, using cache if present."""
    if cache_path.exists():
        cache = pd.read_csv(cache_path)
        cache_map = dict(zip(cache["id"], cache["description"].fillna("")))
        print(f"Loaded {len(cache_map)} cached descriptions from {cache_path}")
    else:
        cache_map = {}

    descriptions = []
    new_scrapes = 0
    for i, row in df.iterrows():
        vote_id = row["id"]
        if vote_id in cache_map and cache_map[vote_id]:
            descriptions.append(cache_map[vote_id])
            continue

        url = row["vote_link"]
        print(f"[{i+1}/{len(df)}] Scraping {vote_id} ...", end=" ", flush=True)
        desc = scrape_description(url)
        if desc:
            print(f"OK ({len(desc)} chars)")
        else:
            print("FAILED")
        descriptions.append(desc or "")
        cache_map[vote_id] = desc or ""
        new_scrapes += 1

        # Periodically flush cache so a crash doesn't lose progress
        if new_scrapes % 25 == 0:
            _save_cache(cache_map, cache_path)
            print(f"  (cache flushed: {len(cache_map)} entries)")

        time.sleep(REQUEST_DELAY)

    _save_cache(cache_map, cache_path)
    df = df.copy()
    df["description"] = descriptions
    return df


def _save_cache(cache_map: dict, path: Path) -> None:
    pd.DataFrame(
        [{"id": k, "description": v} for k, v in cache_map.items()]
    ).to_csv(path, index=False)


# ---------------------------------------------------------------------------
# 2. Keyword classification
# ---------------------------------------------------------------------------

# Keywords are matched as case-insensitive substrings, with word boundaries
# where ambiguity matters (e.g. "fund" alone would match "fundamental").
# Patterns use \b only where needed; many are multi-word and unambiguous.

#
# IMPORTANT: many LCV votes ride on appropriations bills as the vehicle but
# are actually about regulation (e.g. amendments blocking EPA enforcement
# attached to Interior appropriations). So generic words like "appropriations"
# or "fiscal year" are not enough — we look for language describing actual
# funding-level decisions: dollars allocated to programs, reauthorizations
# of conservation funding, named conservation funds, defunding of programs.
#
FUNDING_PATTERNS = [
    r"authorized funding",
    r"funding level",
    r"funding cut",
    r"funding (for|to) (the )?(\w+ ){0,3}(program|fund|conservation|corps)",
    r"appropriates? \$",
    r"appropriated \$",
    r"appropriation of \$",
    r"\$\d[\d,\.]*\s*(million|billion|thousand)\s+(for|to|in|from|over)",
    r"\$\d[\d,\.]*\s*(million|billion)\b",
    r"\bmillion\b\s+(for|to|from|in)\s+(the\s+)?(\w+\s+){0,3}(program|fund|conservation)",
    r"\bbillion\b\s+(for|to|from|in)\s+(the\s+)?(\w+\s+){0,3}(program|fund|conservation)",
    r"reauthoriz\w*\s+(the\s+)?\w+",
    r"land and water conservation fund",
    r"\blwcf\b",
    r"farm bill.*conservation",
    r"conservation reserve program",
    r"environmental quality incentives",
    r"\beqip\b",
    r"trust fund",
    r"\btax credit",
    r"\bgrant program",
    r"\bgrants? to (states|tribal|local|nonprofit|academic)",
    r"\bcost[- ]share",
    r"cut\s+(all\s+)?\$",
    r"cut.{0,30}(funding|program|conservation|appropriation)",
    r"slash(ed|ing)?.{0,30}(funding|program|conservation|appropriation)",
    r"strike (the )?funding",
    r"stricken funding",
    r"striking funding",
    r"strip(ped|ping)?.{0,20}funding",
    r"increase funding (for|to)",
    r"restore.{0,20}funding",
    r"\bdefund\w*",
    r"funding for (conservation|the program|parks|wildlife|water|land)",
    r"dedicated funding",
    r"annual\s+(authorized\s+)?funding",
    r"endowment for",
    r"establish (a |an |the )?\w*\s*(fund|endowment|trust)",
    r"jobs (and )?(infrastructure|conservation) (act|bill).{0,40}funding",
    r"funding package",
    r"funding bill",
    r"transfer (the )?savings",
    r"budget reconciliation",
]

REGULATION_PATTERNS = [
    r"\bregulation",
    r"\bregulatory",
    r"\brule\b",
    r"\brulemaking",
    r"\bstandard",
    r"\bpermit",
    r"\bemission",
    r"\bclean water act",
    r"\bclean air act",
    r"endangered species act",
    r"marine mammal protection",
    r"\bprotection act",
    r"\besa\b",
    r"\blisting",
    r"\bdelisting",
    r"critical habitat",
    r"\bnepa\b",
    r"environmental review",
    r"environmental impact statement",
    r"\beis\b",
    r"\benforce",
    r"\bcompliance",
    r"\brestrict",
    r"\bprohibit",
    r"\bban\b",
    r"requires? (the )?(agency|epa|federal|use)",
    r"agency authority",
    r"agency discretion",
    r"\bdiscretion",
    r"polluter",
    r"\bpollut",
    r"\btoxic",
    r"\bwaters of the united states",
    r"\bwotus\b",
    r"\bmercury\b",
    r"\bmethane",
    r"safe drinking water",
    r"gut.{0,30}(provisions|protections|act|law|rule)",
    r"core provisions",
    r"weaken.{0,30}(law|protection|standard|act|provision|enforcement|rule)",
    r"undermine.{0,30}(protection|law|standard|act|rule)",
    r"eliminate.{0,30}(protection|safeguard|standard|rule|law)",
    r"strip.{0,30}(protection|safeguard|standard|rule|law)",
    r"roll ?back.{0,30}(rule|standard|protection|regulation)",
    r"phase out.{0,30}(harmful|practice|pollut)",
    r"\benvironmental (law|protection|safeguard|standard)",
    r"safety standard",
    r"water quality",
    r"air quality",
    r"\bjurisdiction",
    r"protected.{0,20}(species|wildlife|habitat|water)",
    r"\bharmful practice",
    r"streamlining environmental",
    r"fast[- ]track.{0,30}(permit|review|approval)",
    r"waive.{0,30}(law|federal|state|environmental)",
    r"waiver provision",
    r"transition.{0,30}(away|from).{0,30}(harmful|practice)",
    r"\bbycatch\b",
    r"harmful (fishing|practice|gear)",
    r"\bquota\b",
    r"catch limit",
    r"\bphase.out\b",
    r"\bmoratorium\b",
    r"anti.environmental rider",
    r"environmental rider",
    r"legislative restriction",
    r"\brollback",
    r"rolling back",
    r"weakening.{0,30}(environmental|protection|standard|act)",
]

DESIGNATION_PATTERNS = [
    r"\bwilderness\b",
    r"national monument",
    r"national park\b",
    r"\bdesignat",
    r"\brefuge\b",
    r"\bleasing\b",
    r"oil and gas leas",
    r"\bdrill\w*",
    r"\barctic refuge\b",
    r"\banwr\b",
    r"land exchange",
    r"land transfer",
    r"\bswap\b",
    r"national heritage area",
    r"national recreation area",
    r"national conservation area",
    r"\bmining claim",
    r"\bmining\b",
    r"\bgrazing\b",
    r"\btimber",
    r"management plan",
    r"travel management",
    r"roadless",
    r"wild and scenic",
    r"outer continental shelf",
    r"\bocs\b",
    r"\bmineral withdrawal",
    r"\bmonument\b",
    r"\bestablish.*park",
    r"open.{0,30}to (drilling|mining|leasing|grazing|exploration|development)",
    r"close.{0,30}to (drilling|mining|leasing|grazing|exploration|development)",
    r"sell ?off\b.{0,40}(public|federal|lands?)",
    r"\bsell.{0,40}(public|federal) lands?",
    r"privatize.{0,40}(land|public|federal)",
    r"prioritize?.{0,40}(drill|extract|mining|leasing)",
    r"applications to drill",
    r"\bkeystone\b",
    r"pebble mine",
    r"\bbureau of land management",
    r"\bblm\b",
    r"\bnational wildlife refuge",
]

OTHER_PATTERNS = [
    r"\bconfirmation\b",
    r"\bnominee\b",
    r"\bnomination\b",
    r"\bnominat",
    r"sense of (the )?(senate|house|congress)",
    r"\bresolution of disapproval",
    r"\bprocedural\b",
    r"\bcloture\b",
    r"motion to (table|proceed|recommit)",
]


def _count_matches(text: str, patterns: list[str]) -> int:
    """Return number of distinct patterns that fired in the text."""
    if not text:
        return 0
    text_lower = text.lower()
    count = 0
    for pat in patterns:
        if re.search(pat, text_lower):
            count += 1
    return count


def classify_description(description: str) -> tuple[str, str, dict]:
    """Classify one description.

    Returns
    -------
    (category, confidence, scores_dict)
    """
    if not description or not description.strip():
        return "SCRAPE_FAILED", "low", {}

    scores = {
        "funding_appropriations":      _count_matches(description, FUNDING_PATTERNS),
        "regulation_standards":        _count_matches(description, REGULATION_PATTERNS),
        "land_designation_management": _count_matches(description, DESIGNATION_PATTERNS),
        "other":                       _count_matches(description, OTHER_PATTERNS),
    }

    # Decision rules:
    # 1. If 'other' patterns fire and dominate, classify as other.
    # 2. Otherwise, take the max-scoring category among the substantive three.
    # 3. Confidence is 'high' iff (winner's score >= 2) AND
    #    (winner's score >= 2 * runner-up among substantive cats).
    #    Otherwise 'low'.

    substantive = ["funding_appropriations",
                   "regulation_standards",
                   "land_designation_management"]

    if scores["other"] >= 2 and scores["other"] > max(scores[c] for c in substantive):
        return "other", "high", scores
    if all(scores[c] == 0 for c in substantive) and scores["other"] >= 1:
        return "other", "low", scores
    if all(scores[c] == 0 for c in substantive):
        return "other", "low", scores

    # Pick the winning substantive category
    winner = max(substantive, key=lambda c: scores[c])
    winner_score = scores[winner]
    runner_up = max(s for c, s in scores.items() if c != winner and c in substantive)

    # Confidence logic, calibrated to broader patterns:
    # 'high' if the winner has clear separation from the runner-up.
    #   - winner_score >= 4 (overwhelming evidence), OR
    #   - winner_score >= 3 AND winner_score >= runner_up + 2, OR
    #   - winner_score >= 2 AND runner_up == 0.
    # 'low' otherwise.
    if winner_score >= 4:
        confidence = "high"
    elif winner_score >= 3 and winner_score >= runner_up + 2:
        confidence = "high"
    elif winner_score >= 2 and runner_up == 0:
        confidence = "high"
    else:
        confidence = "low"

    return winner, confidence, scores


def classify_all(df: pd.DataFrame) -> pd.DataFrame:
    cats, confs, score_strs = [], [], []
    for _, row in df.iterrows():
        cat, conf, scores = classify_description(row["description"])
        cats.append(cat)
        confs.append(conf)
        score_strs.append(
            f"fund={scores.get('funding_appropriations', 0)},"
            f"reg={scores.get('regulation_standards', 0)},"
            f"land={scores.get('land_designation_management', 0)},"
            f"other={scores.get('other', 0)}"
        )
    df = df.copy()
    df["mechanism_category"] = cats
    df["classification_confidence"] = confs
    df["keyword_scores"] = score_strs
    return df


# ---------------------------------------------------------------------------
# 3. Reporting
# ---------------------------------------------------------------------------

def print_summary(df: pd.DataFrame) -> None:
    print("\n" + "=" * 70)
    print("MECHANISM CATEGORY BREAKDOWN")
    print("=" * 70)

    total = len(df)
    counts = df["mechanism_category"].value_counts()
    pcts = (counts / total * 100).round(1)
    summary = pd.DataFrame({"n": counts, "pct": pcts})
    summary.loc["TOTAL"] = [total, 100.0]
    print(summary.to_string())

    print("\n" + "-" * 70)
    print("CLASSIFICATION CONFIDENCE")
    print("-" * 70)
    print(df["classification_confidence"].value_counts().to_string())

    n_low = (df["classification_confidence"] == "low").sum()
    print(f"\n→ {n_low} rows flagged low-confidence; review manually before publishing numbers.")

    n_failed = (df["mechanism_category"] == "SCRAPE_FAILED").sum()
    if n_failed:
        print(f"\n→ {n_failed} rows failed to scrape; listed below.")
        failed = df[df["mechanism_category"] == "SCRAPE_FAILED"][
            ["id", "year", "vote_title", "vote_link"]
        ]
        print(failed.to_string(index=False))

    print("\n" + "-" * 70)
    print("BREAKDOWN BY LCV ISSUE TAG")
    print("-" * 70)
    df = df.copy()
    df["categories_clean"] = df["categories"].fillna("").str.strip("[]")
    issue_rows = []
    for issue in ["public_lands", "wildlife", "clean_water", "agriculture"]:
        mask = df["categories_clean"].str.contains(issue, na=False)
        sub = df[mask]
        if len(sub) == 0:
            continue
        row = sub["mechanism_category"].value_counts().to_dict()
        row["total"] = len(sub)
        row["issue"] = issue
        issue_rows.append(row)
    issue_df = pd.DataFrame(issue_rows).set_index("issue")
    cols = ["funding_appropriations", "regulation_standards",
            "land_designation_management", "other", "SCRAPE_FAILED", "total"]
    issue_df = issue_df.reindex(columns=cols).fillna(0).astype(int)
    print(issue_df.to_string())

    print("\n" + "-" * 70)
    print("SAMPLE TITLES PER CATEGORY (for spot-checking)")
    print("-" * 70)
    for cat in ["funding_appropriations", "regulation_standards",
                "land_designation_management", "other"]:
        sub = df[df["mechanism_category"] == cat]
        if len(sub) == 0:
            continue
        print(f"\n[{cat}] ({len(sub)} total)")
        sample = sub.sample(min(6, len(sub)), random_state=42)
        for _, r in sample.iterrows():
            conf = r["classification_confidence"]
            scores = r["keyword_scores"]
            print(f"  ({conf} | {scores}) {r['vote_title']}")


# ---------------------------------------------------------------------------
# 4. Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--input", required=True, help="LCV votes CSV")
    ap.add_argument("--output", required=True, help="Output CSV with category column")
    ap.add_argument("--cache", default="lcv_descriptions_cache.csv",
                    help="Cache file for scraped descriptions")
    ap.add_argument("--limit", type=int, default=None,
                    help="Process only the first N rows (for testing)")
    ap.add_argument("--review-output", default=None,
                    help="Optional: write low-confidence + failed rows to this CSV")
    ap.add_argument("--skip-scrape", action="store_true",
                    help="Don't scrape; use whatever's already in the cache "
                         "(rows missing from cache get empty descriptions)")
    args = ap.parse_args()

    df = pd.read_csv(args.input)
    if args.limit:
        df = df.head(args.limit).copy()
    print(f"Loaded {len(df)} votes from {args.input}")

    cache_path = Path(args.cache)
    if args.skip_scrape:
        if cache_path.exists():
            cache = pd.read_csv(cache_path)
            cmap = dict(zip(cache["id"], cache["description"].fillna("")))
        else:
            cmap = {}
        df = df.copy()
        df["description"] = df["id"].map(lambda i: cmap.get(i, ""))
    else:
        df = scrape_all(df, cache_path)

    df = classify_all(df)

    out_cols = list(df.columns)
    df.to_csv(args.output, index=False, columns=out_cols)
    print(f"\n→ Wrote {args.output}")

    if args.review_output:
        review_mask = (
            (df["classification_confidence"] == "low") |
            (df["mechanism_category"] == "SCRAPE_FAILED")
        )
        df[review_mask].to_csv(args.review_output, index=False)
        print(f"→ Wrote {review_mask.sum()} review rows to {args.review_output}")

    print_summary(df)


if __name__ == "__main__":
    main()
