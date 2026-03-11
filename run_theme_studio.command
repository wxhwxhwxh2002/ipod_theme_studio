#!/bin/zsh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if command -v conda >/dev/null 2>&1; then
  CONDA_BASE="$(conda info --base 2>/dev/null || true)"
  if [ -n "$CONDA_BASE" ] && [ -f "$CONDA_BASE/etc/profile.d/conda.sh" ]; then
    source "$CONDA_BASE/etc/profile.d/conda.sh"
    conda activate ipod_theme
    cd "$SCRIPT_DIR"
    exec python theme_studio.py
  fi
fi

for candidate in \
  "$HOME/miniconda3/envs/ipod_theme/bin/python" \
  "$HOME/anaconda3/envs/ipod_theme/bin/python" \
  "$HOME/mambaforge/envs/ipod_theme/bin/python" \
  "$HOME/miniforge3/envs/ipod_theme/bin/python"
do
  if [ -x "$candidate" ]; then
    cd "$SCRIPT_DIR"
    exec "$candidate" theme_studio.py
  fi
done

echo "Could not find the conda environment 'ipod_theme'."
echo "Run: conda activate ipod_theme && python theme_studio.py"
read "reply?Press Enter to close..."
