#!/bin/bash
# Rebuild every submission artifact from the LIVE paper account, in one command.
#
#   assets/build_all.sh
#
# Run this after the final trading session and before recording the video, so the
# cover, the deck, the write-up and the Alpaca dashboard all show the same numbers.
#
# Produces:
#   assets/cover.png       1920x1080, 16:9   (lablab wants PNG/JPG, 16:9)
#   assets/deck.pptx       9 slides
#   assets/deck.pdf        the same deck     (lablab wants the slides as PDF)
#   docs/ONE_PAGER.md      the required write-up
#   docs/ONE_PAGER.pdf     verified to be exactly one page
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

SOFFICE="${SOFFICE:-/Applications/LibreOffice.app/Contents/MacOS/soffice}"

echo "==> cover"
python3 assets/make_cover.py

echo "==> one-page write-up"
python3 assets/make_onepager.py

echo "==> deck"
node assets/make_deck.js

echo "==> deck -> PDF (lablab asks for the slide presentation in PDF)"
if [[ -x "$SOFFICE" ]]; then
  "$SOFFICE" --headless --convert-to pdf --outdir assets assets/deck.pptx >/dev/null 2>&1
  rm -f assets/.~lock.*#
  pages=$(pdfinfo assets/deck.pdf 2>/dev/null | awk '/^Pages:/{print $2}')
  echo "    assets/deck.pdf (${pages:-?} pages)"
else
  echo "    SKIPPED: LibreOffice not found at $SOFFICE"
  echo "    Export the deck to PDF by hand before submitting - lablab wants PDF slides."
fi

echo
echo "Done. Check the numbers agree with the Alpaca dashboard before you submit."
