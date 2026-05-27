#!bin/bash
cd /home/x-dche/tt_py && .venv/bin/python -m py_compile advisor.py report.py config.py main.py data.py 2>&1 && .venv/bin/python main.py 2>&1
