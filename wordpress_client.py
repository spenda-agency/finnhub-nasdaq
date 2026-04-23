"""WordPress REST API ラッパ（画像アップロード + 投稿下書き作成）"""
import base64
import logging
import mimetypes
import os
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger("wordpress_client")

# 投稿作成/メディアアップロードに最低限必要なWP capability
REQUIRED_CAPS = ("upload_files", "edit_posts")


def _auth_headers() -> dict:
    user = os.getenv("WP_USERNAME")
    app_password = os.getenv("WP_APP_PASSWORD")
    if not user or not app_password:
        raise RuntimeError(
            "WP_USERNAME / WP_APP_PASSWORD が未設定です。"
            "WordPress管理画面 → ユーザー → プロフィール → 「アプリケーションパスワード」を発行してください。"
        )
    # アプリケーションパスワードはスペース区切りで表示されるが、スペースは有意ではない
    # （サーバー側で除去される）。明示的に除去してトークンを安定化させる。
    app_password = app_password.replace(" ", "")
    token = base64.b64encode(f"{user}:{app_password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _site_base_url() -> str:
    base = os.getenv("WP_SITE_URL", "").rstrip("/")
    if not base:
        raise RuntimeError("WP_SITE_URL が未設定です (例: https://example.com)")
    # wp-admin の URL が渡された場合は除去
    if base.endswith("/wp-admin"):
        base = base[: -len("/wp-admin")]
    return base


def _permission_guidance(resp: requests.Response) -> str:
    """401/403 時にユーザーが取るべき対処を返す。"""
    try:
        body = resp.json()
        code = body.get("code", "")
        msg = body.get("message", "")
    except Exception:
        code = ""
        msg = resp.text[:200]

    hints = [
        f"WPサイト: {_site_base_url()}",
        f"WP_USERNAME: {os.getenv('WP_USERNAME', '(未設定)')}",
        f"レスポンス: [{resp.status_code}] code={code} msg={msg}",
    ]

    if code == "rest_cannot_create" or resp.status_code in (401, 403):
        hints.append(
            "対処:\n"
            "  1. WP_USERNAME に対応するユーザーの権限グループが『投稿者(Author)以上』か確認"
            "（購読者/寄稿者では upload_files / edit_posts が不足）\n"
            "  2. アプリケーションパスワードが当該ユーザーで発行されたものか確認"
            "（別ユーザーのApp Passwordを使っていないか）\n"
            "  3. Wordfence / iThemes Security / All-In-One WP Security 等のセキュリティプラグインで"
            "REST APIが制限されていないか確認\n"
            "  4. サーバ (Apache) が Authorization ヘッダを落としていないか確認。"
            "落ちている場合は .htaccess に以下を追加:\n"
            "       SetEnvIf Authorization \"(.*)\" HTTP_AUTHORIZATION=$1"
        )
    elif code == "rest_not_logged_in":
        hints.append(
            "対処: Authorization ヘッダがサーバ側に届いていません。上記の .htaccess 修正、"
            "もしくは WP_APP_PASSWORD の再発行を試してください。"
        )
    return "\n".join(hints)


def verify_credentials() -> dict:
    """WP 認証確認と capability 事前チェック。

    下書き保存前に一度呼ぶと、最終的な API エラーより早く原因特定できる。
    戻り値: /users/me のレスポンス（name, roles, capabilities を含む）
    """
    base = _site_base_url()
    url = f"{base}/wp-json/wp/v2/users/me?context=edit"
    resp = requests.get(url, headers=_auth_headers(), timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(
            "WP 認証チェック失敗。アプリケーションパスワード / ユーザー名を確認してください。\n"
            + _permission_guidance(resp)
        )
    me = resp.json()
    caps = me.get("capabilities", {}) or {}
    missing = [c for c in REQUIRED_CAPS if not caps.get(c)]
    roles = me.get("roles", [])
    log.info(f"WP 認証OK: user={me.get('name')} roles={roles}")
    if missing:
        raise RuntimeError(
            f"WPユーザー '{me.get('name')}' (roles={roles}) に capability {missing} が不足しています。"
            f"『投稿者(Author)』以上のロールに変更してください。"
        )
    return me


def upload_media(image_path: Path, title: str = "") -> int:
    """画像をメディアライブラリにアップロード。アップロードしたメディアIDを返す。"""
    base = _site_base_url()
    url = f"{base}/wp-json/wp/v2/media"
    mime, _ = mimetypes.guess_type(str(image_path))
    mime = mime or "image/png"

    with open(image_path, "rb") as fp:
        headers = _auth_headers()
        headers.update({
            "Content-Disposition": f'attachment; filename="{image_path.name}"',
            "Content-Type": mime,
        })
        resp = requests.post(url, headers=headers, data=fp.read(), timeout=60)

    if resp.status_code not in (200, 201):
        raise RuntimeError(
            f"メディアアップロード失敗 ({resp.status_code})\n{_permission_guidance(resp)}"
        )

    data = resp.json()
    media_id = data.get("id")
    if title:
        # 任意: タイトル更新
        try:
            requests.post(
                f"{url}/{media_id}",
                headers={**_auth_headers(), "Content-Type": "application/json"},
                json={"title": title},
                timeout=30,
            )
        except Exception:
            pass
    return media_id


def create_draft_post(
    title: str,
    content: str,
    featured_media_id: Optional[int] = None,
    categories: Optional[list[int]] = None,
    tags: Optional[list[int]] = None,
    excerpt: Optional[str] = None,
) -> dict:
    """下書き投稿を作成。返り値は WP の投稿オブジェクト（id, link など）。"""
    base = _site_base_url()
    url = f"{base}/wp-json/wp/v2/posts"

    payload = {
        "title": title,
        "content": content,
        "status": "draft",
    }
    if featured_media_id:
        payload["featured_media"] = featured_media_id
    if categories:
        payload["categories"] = categories
    if tags:
        payload["tags"] = tags
    if excerpt:
        payload["excerpt"] = excerpt

    headers = {**_auth_headers(), "Content-Type": "application/json"}
    resp = requests.post(url, headers=headers, json=payload, timeout=60)

    if resp.status_code not in (200, 201):
        raise RuntimeError(
            f"下書き投稿作成失敗 ({resp.status_code})\n{_permission_guidance(resp)}"
        )
    return resp.json()
