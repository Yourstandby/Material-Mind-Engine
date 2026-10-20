"""重置账号密码 —— 忘记密码时的兜底手段（需服务器权限）。

用法：
    python -m scripts.reset_password --list
    python -m scripts.reset_password -u admin
    python -m scripts.reset_password -u admin -p new-pass-123
"""

from __future__ import annotations

import argparse
import getpass
import sys

sys.stdout.reconfigure(encoding="utf-8")

from core import auth


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("-u", "--username", help="账号名")
    parser.add_argument("-p", "--password", help="新密码（不传则交互输入）")
    parser.add_argument("--list", action="store_true", help="只列出账号")
    args = parser.parse_args()

    users = auth.list_users()
    if not users:
        print("还没有任何账号，请先在网页上注册管理员账号")
        return

    if args.list:
        for user in users:
            print(f"{user['id']:>3}  {user['username']:<20} {user['role']:<7} {user['created_at']}")
        return

    if args.username:
        user = auth.find_user(args.username)
        if not user:
            print(f"找不到账号：{args.username}")
            return
    else:
        for item in users:
            print(f"{user['id']:>3}  {user['username']:<20} {item['role']}")
        choice = input("选择账号编号: ").strip()
        user = next((u for u in users if str(u["id"]) == choice), None)
        if not user:
            print("编号无效")
            return

    password = args.password or getpass.getpass("新密码（至少 8 位）: ")
    try:
        auth.reset_password(user["id"], password)
    except ValueError as exc:
        print(f"失败：{exc}")
        return
    print(f"已重置 {user['username']} 的密码，该账号所有设备需重新登录")


if __name__ == "__main__":
    main()
