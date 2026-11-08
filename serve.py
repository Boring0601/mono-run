#!/usr/bin/env python3
"""MONO/RUN static server + tiny persistent leaderboard API.

Usage:
    python serve.py [--port 8080] [--host 127.0.0.1]

API:
    GET  /api/leaderboard[?limit=20&playerId=...]
    POST /api/scores  {"id":"...","name":"...","distance":123}
"""
from __future__ import annotations

import argparse
import json
import os
import re
import threading
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / ".data"
SCORE_FILE = DATA_DIR / "leaderboard.json"
LOCK = threading.RLock()
MAX_NAME = 16
MAX_DISTANCE = 1_000_000
MAX_COINS = 100_000
MAX_BODY = 8192
ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
BOARDS = ("distance", "coins")


def _clean_name(value: object) -> str:
    name = re.sub(r"[\x00-\x1f\x7f]", "", str(value or ""))
    name = re.sub(r"\s+", " ", name).strip()
    return name[:MAX_NAME] or "RUNNER"


def _clean_coins(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    try:
        coins = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(coins, MAX_COINS))


def _read_scores() -> list[dict]:
    with LOCK:
        if not SCORE_FILE.exists():
            return []
        try:
            raw = json.loads(SCORE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(raw, list):
            return []
        scores: list[dict] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            player_id = item.get("id")
            distance = item.get("distance")
            if not isinstance(player_id, str) or not ID_RE.fullmatch(player_id):
                continue
            if isinstance(distance, bool) or not isinstance(distance, int):
                continue
            if distance < 0 or distance > MAX_DISTANCE:
                continue
            scores.append({
                "id": player_id,
                "name": _clean_name(item.get("name")),
                "distance": distance,
                "coins": _clean_coins(item.get("coins")),
                "updatedAt": str(item.get("updatedAt") or ""),
            })
        return scores


def _write_scores(scores: list[dict]) -> None:
    with LOCK:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SCORE_FILE.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(scores, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, SCORE_FILE)


def _ranked(board: str = "distance") -> list[dict]:
    scores = _read_scores()
    if board == "coins":
        scores.sort(key=lambda item: (-item["coins"], -item["distance"], item.get("updatedAt", "")))
    else:
        scores.sort(key=lambda item: (-item["distance"], -item["coins"], item.get("updatedAt", "")))
    return [dict(item, rank=index + 1) for index, item in enumerate(scores)]


def _public(entry: dict) -> dict:
    return {
        "rank": entry["rank"],
        "id": entry["id"],
        "name": entry["name"],
        "distance": entry["distance"],
        "coins": entry.get("coins", 0),
    }


class Handler(SimpleHTTPRequestHandler):
    server_version = "MonoRun/2.0"

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/healthz":
            body = b"ok\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/leaderboard":
            params = parse_qs(parsed.query)
            try:
                limit = int(params.get("limit", ["20"])[0])
            except ValueError:
                limit = 20
            limit = max(1, min(limit, 100))
            player_id = params.get("playerId", [""])[0]
            board = params.get("board", ["distance"])[0]
            if board not in BOARDS:
                board = "distance"
            ranked = _ranked(board)
            player = next((entry for entry in ranked if entry["id"] == player_id), None)
            self._send_json(200, {
                "board": board,
                "entries": [_public(entry) for entry in ranked[:limit]],
                "total": len(ranked),
                "player": _public(player) if player else None,
            })
            return

        if path.startswith("/.data") or path in ("/serve.py",):
            self.send_error(404, "File not found")
            return

        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/api/scores":
            self.send_error(404, "File not found")
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            self._send_json(413, {"ok": False, "error": "invalid body size"})
            return

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._send_json(400, {"ok": False, "error": "invalid json"})
            return
        if not isinstance(payload, dict):
            self._send_json(400, {"ok": False, "error": "invalid payload"})
            return

        player_id = str(payload.get("id", ""))
        if not ID_RE.fullmatch(player_id):
            self._send_json(400, {"ok": False, "error": "invalid player id"})
            return

        raw_distance = payload.get("distance")
        if isinstance(raw_distance, bool):
            self._send_json(400, {"ok": False, "error": "invalid distance"})
            return
        try:
            score = int(raw_distance)
        except (TypeError, ValueError):
            self._send_json(400, {"ok": False, "error": "invalid distance"})
            return
        if score < 0 or score > MAX_DISTANCE:
            self._send_json(400, {"ok": False, "error": "distance out of range"})
            return

        name = _clean_name(payload.get("name"))
        coins = _clean_coins(payload.get("coins"))
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        with LOCK:
            scores = _read_scores()
            existing = next((item for item in scores if item["id"] == player_id), None)
            improved = existing is None or score > existing["distance"]
            improved_coins = existing is None or coins > existing.get("coins", 0)
            if existing is None:
                scores.append({
                    "id": player_id,
                    "name": name,
                    "distance": score,
                    "coins": coins,
                    "updatedAt": now,
                })
            else:
                existing["name"] = name
                if improved:
                    existing["distance"] = score
                    existing["updatedAt"] = now
                if improved_coins:
                    existing["coins"] = coins
            _write_scores(scores)
            ranked = _ranked("distance")
            player = next((entry for entry in ranked if entry["id"] == player_id), None)

        self._send_json(200, {
            "ok": True,
            "improved": improved,
            "improvedCoins": improved_coins,
            "entry": _public(player) if player else None,
            "total": len(ranked),
        })

    def log_message(self, fmt: str, *args) -> None:
        print(f"[mono-run] {self.address_string()} {fmt % args}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve MONO/RUN")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    os.chdir(ROOT)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[mono-run] serving {ROOT} at http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
