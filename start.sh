#!/bin/bash
cd "$(dirname "$0")"
if [ -f cert.crt ] && [ -f cert.key ]; then
    SCHEME="https"
else
    SCHEME="http"
fi
echo "Starting Postdoc Tracker on ${SCHEME}://localhost:3742"
open "${SCHEME}://localhost:3742" &
python3 server.py
