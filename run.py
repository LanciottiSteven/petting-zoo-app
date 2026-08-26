#!/usr/bin/env python3
"""Start the Petting Zoo draft assistant."""
import sys, webbrowser, threading, uvicorn
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8077
    threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    print(f"\n  Petting Zoo draft assistant -> http://127.0.0.1:{port}\n")
    uvicorn.run("pettingzoo.api:app", host="127.0.0.1", port=port, log_level="warning")
