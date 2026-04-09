#!/bin/bash
# Run without Electron — opens the app in your default browser.
cd "$(dirname "$0")"
python3 -m postdoc_tracker "$@"
