"""WordPress REST API ラッパ（画像アップロード + 投稿下書き作成）"""
import base64
import mimetypes
import os
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()


def _auth_headers() -> dict:
    user = os.getenv("WP_USERNAME")
    app_password = os.getenv("WP_APP_PASSWORD")
    if not user or not app_password:
        raise RuntimeError(
            "WP_USERNAME / WP_APP_PASSWORD が未設定です。"
            "WordPress管理画面 → ユーザー → プロフィール → 「アプリケーションパスワード」を発行してください。"
        )
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
        raise RuntimeError(f"メディアアップロード失敗 ({resp.status_code}): {resp.text[:300]}")

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
        raise RuntimeError(f"下書き投稿作成失敗 ({resp.status_code}): {resp.text[:300]}")
    return resp.json()
