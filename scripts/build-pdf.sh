#!/usr/bin/env bash
# Build docs/ into a single PDF handbook.
#
#   ./scripts/build-pdf.sh
#
# Requires pandoc plus one PDF engine. Engines are tried in order of preference;
# typst is first because it is a single ~30 MB binary, where a TeX distribution
# is several hundred MB.
#
# NOTE ON DIAGRAMS: the architecture docs contain ```mermaid blocks. Pandoc has
# no native mermaid support, so those render as source code in the PDF unless
# mermaid-filter is installed (npm i -g mermaid-filter). The script detects it
# and uses it automatically. GitHub renders them natively, so the markdown is
# the better read for diagrams either way.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

DIST="docs/dist"
OUT="$DIST/cli-agent-handbook.pdf"

# ---------------------------------------------------------------- preflight ---

if ! command -v pandoc >/dev/null 2>&1; then
  cat >&2 <<'EOF'
ERROR: pandoc not found.

  macOS:  brew install pandoc
  Linux:  apt install pandoc   (or your package manager)

EOF
  exit 1
fi

ENGINE=""
for candidate in typst tectonic xelatex pdflatex weasyprint; do
  if command -v "$candidate" >/dev/null 2>&1; then
    ENGINE="$candidate"
    break
  fi
done

if [ -z "$ENGINE" ]; then
  cat >&2 <<'EOF'
ERROR: pandoc is installed but no PDF engine was found.

Install ONE of these (typst recommended — single small binary):

  brew install typst          # ~30 MB      <-- recommended
  brew install tectonic       # ~80 MB, self-contained TeX
  brew install basictex       # ~500 MB+, full TeX (provides pdflatex)

EOF
  exit 1
fi

echo "pandoc:      $(pandoc --version | head -1)"
echo "pdf engine:  $ENGINE"

FILTER_ARGS=()
if command -v mermaid-filter >/dev/null 2>&1; then
  FILTER_ARGS+=(--filter mermaid-filter)
  echo "mermaid:     mermaid-filter found — diagrams will render"
else
  echo "mermaid:     not found — diagrams will appear as source (npm i -g mermaid-filter)"
fi

# ------------------------------------------------------------------- inputs ---
# Explicit order, not a glob: the handbook should read start to finish.

FILES=(
  docs/03-architecture/01-plain.md
  docs/00-concepts/anatomy.md
  docs/00-concepts/security.md
  docs/03-architecture/02-beginner.md
  docs/03-architecture/03-production.md
  docs/03-architecture/04-boundaries-and-layout.md
  docs/01-teardown/01-provider-stream.md
  docs/01-teardown/02-agent-loop-tools.md
  docs/01-teardown/03-coding-tools.md
  docs/01-teardown/03b-context-sessions-compaction.md
  docs/01-teardown/04-terminal-ui.md
  docs/01-teardown/05-beyond-the-core.md
  docs/04-folder-trees.md
  docs/04-glossary.md
)

# Include Stage 5's language notes only once it exists.
[ -f docs/05-language-notes.md ] && FILES+=(docs/05-language-notes.md)

MISSING=0
for f in "${FILES[@]}"; do
  if [ ! -f "$f" ]; then
    echo "WARNING: missing, skipping: $f" >&2
    MISSING=1
  fi
done
PRESENT=()
for f in "${FILES[@]}"; do [ -f "$f" ] && PRESENT+=("$f"); done

if [ ${#PRESENT[@]} -eq 0 ]; then
  echo "ERROR: no input documents found. Run this from the repo root." >&2
  exit 1
fi

# -------------------------------------------------------------------- build ---

mkdir -p "$DIST"

pandoc "${PRESENT[@]}" \
  "${FILTER_ARGS[@]+"${FILTER_ARGS[@]}"}" \
  --from=gfm \
  --pdf-engine="$ENGINE" \
  --toc \
  --toc-depth=2 \
  --number-sections \
  --metadata title="Building a Terminal Coding Agent" \
  --metadata subtitle="A study of Pi and Tau, and notes toward an implementation" \
  --metadata author="Rushil Jariwala" \
  --syntax-highlighting=tango \
  -o "$OUT"

SIZE=$(wc -c < "$OUT" | tr -d ' ')
echo
echo "Built $OUT (${SIZE} bytes, ${#PRESENT[@]} documents)"

# Use an if-block, not `[ ... ] && echo`: a false test as the last command
# would become the script's exit status and report a successful build as failed.
if [ "$MISSING" -eq 1 ]; then
  echo "Note: some documents were missing and were skipped (see warnings above)."
fi

exit 0
