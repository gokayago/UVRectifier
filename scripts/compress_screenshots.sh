#!/usr/bin/env zsh
set -euo pipefail

# Compress PNG screenshots to JPEGs under 500KB (approx 0.5 MB).
# Outputs are written to Documentation/Screenshots/compressed (keeps originals).

ROOT_DIR="$PWD"
SCREENSHOT_DIR="$ROOT_DIR/Git_Repo/Documentation/Screenshots"
OUT_DIR="$SCREENSHOT_DIR/compressed"
TARGET_BYTES=500000
mkdir -p "$OUT_DIR"

if ! command -v sips >/dev/null 2>&1; then
  echo "sips is required but not found. This script assumes macOS where sips is available." >&2
  exit 1
fi

# Iterate PNG files safely
find "$SCREENSHOT_DIR" -maxdepth 1 -type f -name '*.png' -print0 | while IFS= read -r -d '' f; do
  base=$(basename "$f" .png)
  out="$OUT_DIR/${base}.jpg"
  echo "Processing: ${base}.png -> ${base}.jpg"

  # Try decreasing quality levels until under target size
  for q in 90 80 70 60 50 40 30 20; do
    sips -s format jpeg -s formatOptions $q "$f" --out "$out" >/dev/null
    size=$(stat -f%z "$out")
    if [ "$size" -le $TARGET_BYTES ]; then
      echo "  -> quality=$q size=$(du -h "$out" | cut -f1)"
      break
    fi
  done

  # If still too large and jpegoptim is available, try to further shrink
  if [ $(stat -f%z "$out") -gt $TARGET_BYTES ] && command -v jpegoptim >/dev/null 2>&1; then
    jpegoptim --size=500k --strip-all --max=85 "$out" >/dev/null || true
    echo "  -> jpegoptim applied, new size=$(du -h "$out" | cut -f1)"
  fi

  # Final size report
  final_size=$(stat -f%z "$out")
  echo "  Final: $(du -h "$out" | cut -f1) (${final_size} bytes)"

  # If still too large, warn the user
  if [ $final_size -gt $TARGET_BYTES ]; then
    echo "  WARNING: ${out} is still larger than 500KB. Consider manual adjustments or converting to WebP." >&2
  fi

done

echo "Compression complete. Outputs in: $OUT_DIR"
