"""Run the browser version without importing or starting the Telegram bot."""

import argparse
from pathlib import Path
import threading
import webbrowser

import uvicorn

from web_app.server import create_app


def main():
    parser = argparse.ArgumentParser(description="YouTube Local — загрузчик в браузере")
    parser.add_argument("--port", type=int, default=8080, help="локальный порт (по умолчанию 8080)")
    parser.add_argument("--no-browser", action="store_true", help="не открывать браузер автоматически")
    parser.add_argument("--download-dir", type=Path, help="папка для временных файлов и истории")
    parser.add_argument("--save-dir", type=Path, help="папка готовых файлов (по умолчанию ~/Downloads)")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("порт должен быть от 1 до 65535")
    app = create_app(args.download_dir, args.save_dir)
    config = uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="info", access_log=False)
    server = uvicorn.Server(config)
    if not args.no_browser:
        def open_when_ready():
            import time
            while not server.started and not server.should_exit:
                time.sleep(0.1)
            if server.started:
                webbrowser.open(f"http://127.0.0.1:{args.port}")
        threading.Thread(target=open_when_ready, daemon=True).start()
    try:
        server.run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
