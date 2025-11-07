@echo off
python -m venv .venv
call .venv\Scripts\activate
pip install -r requirements.txt
IF NOT EXIST .env (
  copy .env.template .env
)
python livemesterbot.py
