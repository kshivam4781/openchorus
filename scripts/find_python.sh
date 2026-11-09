# shellcheck shell=bash
# Sourced by the .sh launchers. Sets PY to a Python 3.10+ interpreter or exits with help.
# macOS ships Python 3.9 as `python3`, so look for newer ones first.
PY=""
for cand in python3.13 python3.12 python3.11 python3.10 python3 python; do
  if command -v "$cand" >/dev/null 2>&1 \
     && "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PY="$cand"; break
  fi
done
if [ -z "$PY" ]; then
  echo "OpenChorus needs Python 3.10 or newer."
  case "$(uname)" in
    Darwin) echo "Install it with: brew install python@3.12   (or from https://www.python.org/downloads/)";;
    *)      echo "Install it with your package manager, e.g.: sudo apt install python3.12 python3.12-venv";;
  esac
  exit 1
fi

make_venv() {  # make_venv <dir>: create the virtual environment with a clear hint if venv is missing
  if [ -d "$1" ]; then return 0; fi
  if ! "$PY" -m venv "$1" 2>/dev/null; then
    rm -rf "$1"
    echo "Could not create a virtual environment with $PY."
    echo "On Debian/Ubuntu install the venv module: sudo apt install python3-venv"
    exit 1
  fi
  "$1/bin/python" -m pip install -q --upgrade pip
}

check_ffmpeg() {
  if ! command -v ffmpeg >/dev/null 2>&1; then
    case "$(uname)" in
      Darwin) echo "Note: ffmpeg not found. Install it for MP3 output, mastering and uploads: brew install ffmpeg";;
      *)      echo "Note: ffmpeg not found. Install it for MP3 output, mastering and uploads: sudo apt install ffmpeg";;
    esac
  fi
}
