#!/bin/sh
# simple startup script for Railway / Railpack
cd backend || exit 1
# install deps (railway sometimes runs build in Docker; reinstalling is safe)
pip install -r requirements.txt
# run uvicorn (Railway provides $PORT env)
python -m uvicorn app:app --host 0.0.0.0 --port "${PORT:-8000}"
