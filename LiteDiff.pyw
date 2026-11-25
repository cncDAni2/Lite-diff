"""LiteDiff indító: az alap pythonw-val fut (külön venv-indító folyamat nélkül), a venv csomagjaival."""
import os
import site
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
site.addsitedir(os.path.join(HERE, ".venv", "Lib", "site-packages"))
sys.path.insert(0, HERE)

from litediff.app import main

main()