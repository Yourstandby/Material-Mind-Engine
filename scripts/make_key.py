"""发放 / 查看 / 吊销网关 API Key。

用法：
    python -m scripts.make_key --name team-a --rpm 120
    python -m scripts.make_key --list
    python -m scripts.make_key --revoke 2
"""

from __future__ import annotations

import argparse
import sys

sys.stdout.reconfigure(encoding="utf-8")

from gateway import store


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", help="使用方名称")
    parser.add_argument("--rpm", type=int, default=120, help="每分钟请求上限")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--revoke", help="按 key 或 id 吊销")
    args = parser.parse_args()

    if args.list:
        for item in store.list_keys():
            flag = "启用" if item["active"] else "停用"
            print(f"{item['id']:>3}  {item['name']:<18} {item['prefix']}…  {item['rpm']:>4}/min  {flag}  {item['created_at']}")
        return

    if args.revoke:
        print("已吊销" if store.revoke(args.revoke) else "未找到")
        return

    if not args.name:
        parser.error("需要 --name，或用 --list / --revoke")

    key = store.create_key(args.name, args.rpm)
    print("API Key（只显示这一次，请立即保存）：")
    print(key)
    print(f"\n限速 {args.rpm} 次/分钟 · 使用方 {args.name}")


if __name__ == "__main__":
    main()
