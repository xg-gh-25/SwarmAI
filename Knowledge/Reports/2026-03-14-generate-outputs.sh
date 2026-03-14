#!/bin/bash
# Generate PDF and long-image from Swarm's birthday HTML
# Run this in your terminal (outside Claude sandbox):
#   bash ~/swarm-ai/SwarmWS/Knowledge/Reports/2026-03-14-generate-outputs.sh

DIR="$HOME/.swarm-ai/SwarmWS/Knowledge/Reports"
HTML="$DIR/2026-03-14-swarm-birthday-final.html"
PDF="$DIR/2026-03-14-swarm-birthday-session.pdf"
PNG="$DIR/2026-03-14-swarm-birthday-poster.png"

echo "Generating PDF..."
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless --disable-gpu --no-sandbox \
  --print-to-pdf="$PDF" --print-to-pdf-no-header \
  "file://$HTML" 2>/dev/null

if [ -f "$PDF" ]; then
  echo "PDF: $PDF ($(du -h "$PDF" | cut -f1))"
else
  echo "Chrome failed. Try: open '$HTML' then Cmd+P -> Save as PDF"
fi

echo ""
echo "Generating long-image PNG..."
# Use Chrome screenshot (requires --window-size for full page)
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless --disable-gpu --no-sandbox \
  --screenshot="$PNG" --window-size=800,30000 \
  "file://$HTML" 2>/dev/null

if [ -f "$PNG" ]; then
  echo "PNG: $PNG ($(du -h "$PNG" | cut -f1))"
else
  echo "Screenshot failed. Alternative: open HTML in browser -> use Full Page Screenshot extension"
fi

echo ""
echo "All files in: $DIR"
ls -lh "$DIR"/2026-03-14-swarm-birthday-*
