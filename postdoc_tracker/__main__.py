import argparse
from .server import run, PORT

def main():
    parser = argparse.ArgumentParser(description="Postdoc Tracker")
    parser.add_argument("--port", type=int, default=PORT, help="Port to listen on")
    parser.add_argument("--no-browser", action="store_true", help="Don't open the browser")
    parser.add_argument("--http", action="store_true", help="Force HTTP even if certs exist")
    args = parser.parse_args()
    run(port=args.port, open_browser=not args.no_browser, force_http=args.http)

if __name__ == "__main__":
    main()
