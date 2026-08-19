#!/usr/bin/env bash
set -e
exec gunicorn main:app --timeout 120 --workers 1 --threads 4
