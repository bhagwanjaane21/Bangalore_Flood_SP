"""Compatibility entry point: finite progressing-surge demo in an isolated database.

Use simulate.py --list for all scenarios. Importing this file performs no I/O.
"""

from simulation.cli import main


if __name__ == "__main__":
    raise SystemExit(main(default_scenario="progressing-surge"))
