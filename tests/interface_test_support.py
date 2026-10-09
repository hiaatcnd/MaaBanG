"""Inspect the effective UI options while keeping the checked-in PI compact."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from song_interface import load_interface, expand_interface
