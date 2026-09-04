#!/usr/bin/env bash
# publish_site.sh — build the static site and publish it to the gh-pages branch.
#
# Usage:
#   scripts/publish_site.sh              # full build, then commit + push gh-pages
#   scripts/publish_site.sh --skip-build # publish whatever is already in site/
#   scripts/publish_site.sh --dry-run    # build + stage, show the diff, do not push
#
# Why a separate branch: GitHub Pages can only serve a branch *root* or that branch's
# /docs folder — never an arbitrary /site. `.gitignore` keeps `/site` off `main`, so the
# build output is published to an orphan `gh-pages` branch through a git worktree; `main`'s
# working tree is never touched.
#
# The orphan branch is rebuilt as a SINGLE commit and force-pushed on every run. The site is
# ~3 MB of derived output; keeping daily history would add ~1 GB/year of git objects for no
# audit value — `main` is the audit trail. This mirrors what actions-gh-pages does with
# force_orphan.
#
# One-time manual step (cannot be done from here): in GitHub → Settings → Pages, set the
# source to branch `gh-pages`, folder `/ (root)`, and re-add the custom domain. CNAME is
# re-emitted by every build, so the domain survives each deploy.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SITE_DIR="site"
BRANCH="gh-pages"
WORKTREE=".worktrees/gh-pages"

SKIP_BUILD=0
DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --skip-build) SKIP_BUILD=1 ;;
        --dry-run)    DRY_RUN=1 ;;
        -h|--help)    sed -n '2,25p' "$0"; exit 0 ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

# ---------------------------------------------------------------- build
if [ "$SKIP_BUILD" -eq 0 ]; then
    log "Building site (this takes ~20-40 min: generate_site.py scans full price history)"
    # shellcheck disable=SC1091
    source venv/bin/activate

    # Order matters — baskets first; see readme.md "Build".
    python build_price_flags.py
    python build_baskets.py
    python build_anomalies.py
    python build_categories.py
    python build_cpi.py
    python build_stores_index.py
    python build_uat_geojson.py
    python export_analytics.py
    python generate_site.py
    python generate_pipeline_report.py
else
    log "Skipping build (--skip-build)"
fi

# ---------------------------------------------------------------- sanity
# A publish that ships an unstyled or empty site is worse than no publish: it silently
# replaces a working site. Refuse rather than deploy a broken tree.
for required in index.html assets/app.css assets/charts.js assets/logo.svg CNAME; do
    [ -s "$SITE_DIR/$required" ] || { echo "FATAL: $SITE_DIR/$required missing or empty — refusing to publish." >&2; exit 1; }
done
SRC_COUNT="$(find "$SITE_DIR" -type f | wc -l)"
[ "$SRC_COUNT" -ge 20 ] || { echo "FATAL: only $SRC_COUNT files in $SITE_DIR/ — refusing to publish a near-empty site." >&2; exit 1; }
log "Publishing $SRC_COUNT files from $SITE_DIR/"

# ---------------------------------------------------------------- worktree
# Recreate the worktree each run so a half-finished previous deploy can't leak into this one.
git worktree remove --force "$WORKTREE" 2>/dev/null || true
rm -rf "$WORKTREE"
mkdir -p "$(dirname "$WORKTREE")"

if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
    git worktree add --force "$WORKTREE" "$BRANCH" >/dev/null
else
    git worktree add --force --detach "$WORKTREE" >/dev/null
    git -C "$WORKTREE" checkout --orphan "$BRANCH" >/dev/null 2>&1
fi

# Wipe the worktree so deleted pages actually disappear from the published site.
git -C "$WORKTREE" rm -rqf . 2>/dev/null || true
find "$WORKTREE" -mindepth 1 -maxdepth 1 ! -name '.git' -exec rm -rf {} +

cp -a "$SITE_DIR/." "$WORKTREE/"
# Without this, Pages runs Jekyll, which drops files and directories beginning with "_".
touch "$WORKTREE/.nojekyll"

# ---------------------------------------------------------------- stage & verify
git -C "$WORKTREE" add --all

# `main`'s .gitignore does not apply here (separate working tree, orphan branch), but a
# stray global excludesFile or info/exclude could still silently drop files. Compare counts
# rather than trusting that.
STAGED="$(git -C "$WORKTREE" ls-files | wc -l)"
EXPECTED=$((SRC_COUNT + 1))   # +1 for .nojekyll
if [ "$STAGED" -ne "$EXPECTED" ]; then
    echo "FATAL: staged $STAGED files but expected $EXPECTED — a gitignore rule is eating output." >&2
    echo "Ignored files:" >&2
    git -C "$WORKTREE" ls-files --others --ignored --exclude-standard | head -20 >&2
    exit 1
fi

# "No local changes" is not the same as "the remote is up to date": a previous run may have
# committed and then failed to push. Only skip when the remote actually has this content.
REMOTE_SHA="$(git ls-remote origin "refs/heads/$BRANCH" 2>/dev/null | cut -f1)"
LOCAL_SHA="$(git -C "$WORKTREE" rev-parse HEAD 2>/dev/null || true)"
if git -C "$WORKTREE" diff --cached --quiet && [ -n "$REMOTE_SHA" ] && [ "$REMOTE_SHA" = "$LOCAL_SHA" ]; then
    log "No changes to publish — site is already up to date."
    git worktree remove --force "$WORKTREE"
    exit 0
fi
if git -C "$WORKTREE" diff --cached --quiet; then
    log "Content unchanged, but origin/$BRANCH is missing or behind — republishing."
fi
git -C "$WORKTREE" diff --cached --stat | tail -5

if [ "$DRY_RUN" -eq 1 ]; then
    log "DRY RUN — staged $STAGED files in $WORKTREE, nothing pushed."
    exit 0
fi

# ---------------------------------------------------------------- commit & push
SRC_SHA="$(git rev-parse --short HEAD)"

# Single-commit orphan branch (see header). `checkout --orphan` keeps the staged index, so
# the files verified above carry straight into the new root commit — no second commit, and
# no history to accumulate.
git -C "$WORKTREE" checkout -q --orphan __squash
git -C "$WORKTREE" add -A
git -C "$WORKTREE" commit -q -m "site: build $(date -u +%Y-%m-%dT%H:%MZ) from ${SRC_SHA}"
git -C "$WORKTREE" branch -qM "$BRANCH"

git -C "$WORKTREE" push --force -q origin "$BRANCH"
log "Pushed $STAGED files to origin/$BRANCH (from main@${SRC_SHA})"

git worktree remove --force "$WORKTREE"
