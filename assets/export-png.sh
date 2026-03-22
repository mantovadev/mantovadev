#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

svg_dir="${ROOT_DIR}/assets/svg"
out_dir=""

declare -a widths=()

usage() {
  cat <<'EOF'
Export PNG files from SVG assets using Inkscape.

Usage:
  assets/export-png.sh [options]

Options:
  -w, --width <px>       Export one PNG for each width (repeatable)
  -o, --out-dir <path>   Output PNG directory (required)
  -h, --help             Show this help

Examples:
  assets/export-png.sh -o /tmp/mantovadev-png
  assets/export-png.sh -o /tmp/mantovadev-png --width 512 --width 1024
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -w|--width)
      [[ $# -ge 2 ]] || { echo "Missing value for $1" >&2; exit 1; }
      widths+=("$2")
      shift 2
      ;;
    -o|--out-dir)
      [[ $# -ge 2 ]] || { echo "Missing value for $1" >&2; exit 1; }
      out_dir="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

[[ -d "$svg_dir" ]] || { echo "SVG directory not found: $svg_dir" >&2; exit 1; }
[[ -n "$out_dir" ]] || { echo "Missing required option: --out-dir <path>" >&2; exit 1; }
mkdir -p "$out_dir"

shopt -s nullglob
svgs=("$svg_dir"/*.svg)

if [[ ${#svgs[@]} -eq 0 ]]; then
  echo "No SVG files found in: $svg_dir" >&2
  exit 1
fi

exported=0

for svg in "${svgs[@]}"; do
  base_name="$(basename "$svg" .svg)"

  if [[ ${#widths[@]} -eq 0 ]]; then
    inkscape "$svg" \
      --export-type=png \
      --export-filename="$out_dir/${base_name}.png" >/dev/null
    ((exported += 1))
    continue
  fi

  for w in "${widths[@]}"; do
    inkscape "$svg" \
      --export-type=png \
      --export-width="$w" \
      --export-filename="$out_dir/${base_name}-w${w}.png" >/dev/null
    ((exported += 1))
  done
done

echo "Export complete: ${exported} PNG file(s) generated in ${out_dir}."
