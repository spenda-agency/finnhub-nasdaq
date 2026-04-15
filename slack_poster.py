"""Slackへの画像アップロード"""
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

load_dotenv()

# Slack channel ID の正規表現（C/G/D/Z で始まる英数字9文字以上）
CHANNEL_ID_PATTERN = re.compile(r"^[CGDZ][A-Z0-9]{8,}$")


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
    client = WebClient(token=token)
    channel_id = _resolve_channel_id(client, channel)
    return client, channel_id


def post_chart_to_slack(image_path: Path, caption: str, thread_ts: str = None) -> str:
    """PNGをSlackにアップロード。スレッド指定時はスレッドにぶら下げる。"""
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
        print(f"  OK Slack投稿: {result.get('file', {}).get('permalink', '')}")
        return result.get("file", {}).get("shares", {}).get("public", {}).get(channel_id, [{}])[0].get("ts", "")
    except SlackApiError as e:
        print(f"  NG Slack投稿失敗: {e.response['error']}")
        raise


def post_text_to_slack(message: str, thread_ts: str = None) -> str:
    """テキストメッセージを投稿。スレッド親メッセージとしても使える。
    返り値は ts（後続の thread_ts に使える）。"""
    client, channel_id = _client_or_raise()
    try:
        kwargs = dict(channel=channel_id, text=message, mrkdwn=True)
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        result = client.chat_postMessage(**kwargs)
        ts = result.get("ts", "")
        print(f"  OK テキスト投稿 ts={ts}")
        return ts
    except SlackApiError as e:
        print(f"  NG テキスト投稿失敗: {e.response['error']}")
        raise
