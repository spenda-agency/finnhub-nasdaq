"""Slackへの画像アップロード / テキスト投稿。

このモジュールは2系統の API を持つ：

- `post_text_to_slack` / `post_chart_to_slack`
    失敗時に例外を投げる「strict」版。明示的に失敗を握りたい呼び出し側向け。
- `try_post_text` / `try_post_chart`
    失敗を握り潰して None を返す「safe」版。runner〜Slack 間の一時的ネットワーク
    断で morning_report 全体が落ちないように、通常フロー・except 節からの通知は
    こちらを使う。

加えて `preflight_slack()` で起動時に `auth.test` を短いタイムアウトで叩き、
到達不可なら以降の Slack 呼び出しを全てスキップ（drafts / WordPress の処理は
継続）するモードに切り替える。`is_slack_enabled()` で状態を参照できる。
"""
import logging
import os
import re
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

load_dotenv()
log = logging.getLogger("slack_poster")

# Slack channel ID の正規表現（C/G/D/Z で始まる英数字9文字以上）
CHANNEL_ID_PATTERN = re.compile(r"^[CGDZ][A-Z0-9]{8,}$")

# WebClient のソケット timeout（秒）。urllib デフォルト（≒270秒）だと
# 1回の chat.postMessage 失敗で数分単位のハングを招くため、短く明示する。
_WEBCLIENT_TIMEOUT_SEC = 15

# モジュール内グローバル: preflight 失敗時に True になり、safe 版が早期 return する。
_SLACK_DISABLED = False
# 抑制された Slack 呼び出しの件数（終了時のサマリ用）
_SUPPRESSED_COUNT = 0


def is_slack_enabled() -> bool:
    """preflight 失敗で Slack が無効化されていないかを返す。"""
    return not _SLACK_DISABLED


def suppressed_count() -> int:
    """safe 版でこれまでに握り潰された失敗件数を返す。"""
    return _SUPPRESSED_COUNT


def disable_slack(reason: str = "") -> None:
    """以降の safe 版投稿を全てスキップさせる。"""
    global _SLACK_DISABLED
    _SLACK_DISABLED = True
    log.warning(f"Slack 投稿を無効化（以降の safe 投稿はスキップ）: {reason}")


def _resolve_channel_id(client: WebClient, channel: str) -> str:
    """チャンネル名からIDを解決する。IDが渡された場合はそのまま返す。

    注意: 名前解決には `channels:read` (public) / `groups:read` (private) スコープが必要。
    """
    # 既にIDならそのまま
    if CHANNEL_ID_PATTERN.match(channel):
        return channel

    # 先頭の # を除去
    name = channel.lstrip("#").strip()

    cursor = None
    while True:
        resp = client.conversations_list(
            exclude_archived=True,
            types="public_channel,private_channel",
            limit=200,
            cursor=cursor,
        )
        for ch in resp.get("channels", []):
            if ch.get("name") == name:
                return ch["id"]
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break

    raise RuntimeError(
        f"チャンネル '{channel}' が見つかりません。"
        f".env の SLACK_CHANNEL をチャンネルID（C... 形式）で指定するか、"
        f"Botに channels:read スコープを付与してチャンネルに招待してください。"
    )


def _client_or_raise() -> tuple[WebClient, str]:
    token = os.getenv("SLACK_BOT_TOKEN")
    channel = os.getenv("SLACK_CHANNEL")
    missing = []
    if not token:
        missing.append("SLACK_BOT_TOKEN")
    if not channel:
        missing.append("SLACK_CHANNEL")
    if missing:
        raise RuntimeError(
            f"環境変数 {', '.join(missing)} が未設定です。"
            f"ローカル実行時は .env を、GitHub Actions実行時は Settings→Secrets を確認してください。"
        )
    # timeout を明示し、slack_sdk のデフォルトリトライハンドラを抑制する。
    # （リトライ込みで長時間ハングするのを防ぐ。必要なら safe 版で 1 回だけリトライを足す）
    client = WebClient(
        token=token,
        timeout=_WEBCLIENT_TIMEOUT_SEC,
        retry_handlers=[],
    )
    channel_id = _resolve_channel_id(client, channel)
    return client, channel_id


def preflight_slack() -> bool:
    """起動時に Slack 到達性を確認する。

    `auth.test` を短い timeout で叩き、成功なら True、失敗なら False を返す。
    失敗時は `disable_slack()` を呼んで以降の safe 投稿を全てスキップ状態にする。
    """
    global _SLACK_DISABLED
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        disable_slack("SLACK_BOT_TOKEN 未設定")
        return False
    try:
        # auth.test 専用に短い timeout で WebClient を作る
        probe = WebClient(token=token, timeout=8, retry_handlers=[])
        resp = probe.auth_test()
        if resp.get("ok"):
            log.info(
                f"Slack preflight OK: team={resp.get('team', '')} "
                f"user={resp.get('user', '')}"
            )
            return True
        disable_slack(f"auth.test ok=false: {resp.data}")
        return False
    except Exception as e:
        disable_slack(f"auth.test 失敗: {e}")
        return False


def post_chart_to_slack(image_path: Path, caption: str, thread_ts: Optional[str] = None) -> str:
    """PNGをSlackにアップロード。スレッド指定時はスレッドにぶら下げる。

    strict 版（失敗時に例外）。
    """
    client, channel_id = _client_or_raise()
    try:
        kwargs = dict(
            channel=channel_id,
            file=str(image_path),
            filename=image_path.name,
            initial_comment=caption,
        )
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        result = client.files_upload_v2(**kwargs)
        log.info(f"  OK Slack投稿: {result.get('file', {}).get('permalink', '')}")
        return result.get("file", {}).get("shares", {}).get("public", {}).get(channel_id, [{}])[0].get("ts", "")
    except SlackApiError as e:
        log.warning(f"  NG Slack投稿失敗: {e.response['error']}")
        raise


def post_text_to_slack(message: str, thread_ts: Optional[str] = None) -> str:
    """テキストメッセージを投稿。スレッド親メッセージとしても使える。

    strict 版（失敗時に例外）。返り値は ts（後続の thread_ts に使える）。
    """
    client, channel_id = _client_or_raise()
    try:
        kwargs = dict(channel=channel_id, text=message, mrkdwn=True)
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        result = client.chat_postMessage(**kwargs)
        ts = result.get("ts", "")
        log.info(f"  OK テキスト投稿 ts={ts}")
        return ts
    except SlackApiError as e:
        log.warning(f"  NG テキスト投稿失敗: {e.response['error']}")
        raise


def try_post_text(message: str, thread_ts: Optional[str] = None) -> Optional[str]:
    """テキスト投稿の safe 版。失敗時は None を返し、例外は伝播させない。

    `preflight_slack()` で無効化されている場合も即 None を返す。
    """
    global _SUPPRESSED_COUNT
    if _SLACK_DISABLED:
        _SUPPRESSED_COUNT += 1
        return None
    try:
        return post_text_to_slack(message, thread_ts=thread_ts)
    except Exception as e:
        _SUPPRESSED_COUNT += 1
        log.warning(f"  NG Slack post 抑制: {type(e).__name__}: {e}")
        return None


def try_post_chart(image_path: Path, caption: str, thread_ts: Optional[str] = None) -> Optional[str]:
    """画像投稿の safe 版。失敗時は None を返し、例外は伝播させない。"""
    global _SUPPRESSED_COUNT
    if _SLACK_DISABLED:
        _SUPPRESSED_COUNT += 1
        return None
    try:
        return post_chart_to_slack(image_path, caption, thread_ts=thread_ts)
    except Exception as e:
        _SUPPRESSED_COUNT += 1
        log.warning(f"  NG Slack chart 抑制: {type(e).__name__}: {e}")
        return None
