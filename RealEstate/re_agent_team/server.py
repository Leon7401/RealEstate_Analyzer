#!/usr/bin/env python3
"""
不動産投資判定システム - スタンドアローンサーバー

使い方:
    python server.py                    # デフォルト (127.0.0.1:8080)
    python server.py --host 0.0.0.0     # 外部公開
    python server.py --port 3000        # ポート変更
    python server.py --reload           # 開発モード（ファイル変更で自動リロード）
"""
import argparse
import sys
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).resolve().parent))


def main():
    parser = argparse.ArgumentParser(description="不動産投資判定システム サーバー")
    parser.add_argument("--host", default="127.0.0.1", help="バインドアドレス (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="ポート (default: 8080)")
    parser.add_argument("--reload", action="store_true", help="開発モード（自動リロード）")
    parser.add_argument("--workers", type=int, default=1, help="ワーカー数 (default: 1)")
    args = parser.parse_args()

    import uvicorn

    print(f"=" * 50)
    print(f"  不動産投資判定システム v2.0")
    print(f"  http://{args.host}:{args.port}")
    print(f"=" * 50)

    uvicorn.run(
        "web.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=args.workers,
        log_level="info",
    )


if __name__ == "__main__":
    main()
