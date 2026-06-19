#!/usr/bin/env python3
"""
詩考「聴く詩考」：話者ラベル付きの対談台本（today_dialogue.txt）を、
2人の声（女性＝聞き手／男性＝読み解き手）で読み分けた1本のmp3にし、
ポッドキャストRSSフィードを更新して GitHub Pages で配信する。

  使い方:  python generate_podcast_dialogue.py today_dialogue.txt sugao
           （第2引数 = 公開ファイル名のスラッグ。省略時は "ep"）

台本フォーマット（プレーンテキスト・歌詞は一切含めない）:
  # 素顔                      ← 先頭の「# 」行はエピソード題（任意）
  聞き手: 今日読み解くのは……
  読み解き手: この曲の主人公は……
  聞き手: それは、どういうこと？
  読み解き手: ……
  読み解き手: ※本稿はAIによるひとつの解釈です。あなたの聴き方が、別の答えを持っているかもしれません。

依存:  pip install edge-tts mutagen   （TTSは無料・APIキー不要）
※ 公開前に check_dialogue.py で歌詞ゼロ・「話者」不使用・末尾固定文を検査すること。
"""

import os
import re
import sys
import json
import html
import shutil
import asyncio
import subprocess
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from pathlib import Path

import edge_tts

try:                                    # 同梱ffmpeg（滑らかな結合に使用）
    import imageio_ffmpeg
    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG = None

# ============ 設定（ここだけ書き換える） ============
REPO_DIR   = Path(__file__).resolve().parent
BASE_URL   = "https://miopapa0424-dotcom.github.io/utakou-listen"   # 末尾スラッシュ無し
SPEAKERS   = {                       # 話者ラベル → 声
    "聞き手":   "ja-JP-NanamiNeural",   # 女性・進行・素朴な問い
    "読み解き手": "ja-JP-KeitaNeural",    # 男性・解釈を噛み砕く
}
DEFAULT_SPEAKER = "聞き手"
# 読み上げ専用の読みかえ（表示タイトルには影響しない）。詩考＝うたこう。
READINGS = {
    "詩考": "うたこう",
}
RATE       = "-4%"                   # ほんの少しゆっくり＝解説向きで落ち着いた口調
PAUSE_SEC  = 0.4                     # 話者の切り替わりに入れる自然な“間”（秒）
KEEP_N     = 200                     # 残すエピソード数（詩考はアーカイブ的に長く保持）
PODCAST_TITLE  = "聴く詩考"
PODCAST_AUTHOR = "やすひろ"
PODCAST_DESC   = "歌詞の解釈を、二人の声で読み解く番組。AIによるひとつの解釈です。"
PODCAST_LANG   = "ja"
JST = timezone(timedelta(hours=9))
# ====================================================

PUBLIC_DIR = REPO_DIR / "public"
EP_DIR     = PUBLIC_DIR / "episodes"
META       = PUBLIC_DIR / "episodes.json"


def clean_for_speech(text: str) -> str:
    """読み上げに不要な記号・マークダウン・URLを除去（歌詞記号は台本に無い前提）。"""
    text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)
    text = re.sub(r"https?://[^\s)]+", "", text)
    text = re.sub(r"[`*_#>|]", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    for kanji, yomi in READINGS.items():          # 音声だけ読みかえ（詩考→うたこう 等）
        text = text.replace(kanji, yomi)
    return text.strip()


def parse_dialogue(raw: str):
    """台本テキストを (title, [(speaker, text), ...]) に分解する。"""
    title = None
    turns = []
    cur_speaker = None
    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):                       # 先頭の題（任意）
            if title is None:
                title = s.lstrip("# ").strip()
            continue
        m = re.match(r"^([^:：]{1,8})[:：]\s*(.*)$", s)
        if m and m.group(1).strip() in SPEAKERS:
            cur_speaker = m.group(1).strip()
            body = m.group(2).strip()
            if body:
                turns.append([cur_speaker, body])
        else:
            # ラベルの無い続き行 → 直前の話者に連結（無ければ既定話者）
            if turns and cur_speaker:
                turns[-1][1] += " " + s
            else:
                turns.append([cur_speaker or DEFAULT_SPEAKER, s])
    # 読み上げ整形
    turns = [(sp, clean_for_speech(tx)) for sp, tx in turns if clean_for_speech(tx)]
    return title, turns


async def synth_segment(text: str, voice: str, out_path: Path):
    await edge_tts.Communicate(text, voice, rate=RATE).save(str(out_path))


def seg_duration(path: Path, fallback_text: str = "") -> float:
    try:
        from mutagen.mp3 import MP3
        return float(MP3(str(path)).info.length)
    except Exception:
        return max(1.0, len(fallback_text) / 6.5)


def _stitch_with_ffmpeg(seg_paths, tmp_dir: Path, out_path: Path) -> float:
    """話者間に“間”を入れ、再エンコードして継ぎ目を均した1本のmp3にする。"""
    silence = tmp_dir / "_sil.mp3"
    subprocess.run([FFMPEG, "-y", "-f", "lavfi", "-t", str(PAUSE_SEC),
                    "-i", "anullsrc=r=24000:cl=mono",
                    "-c:a", "libmp3lame", "-b:a", "64k", str(silence)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    lines = []
    for i, seg in enumerate(seg_paths):
        lines.append(f"file '{seg.as_posix()}'")
        if i != len(seg_paths) - 1:
            lines.append(f"file '{silence.as_posix()}'")
    listing = tmp_dir / "list.txt"
    listing.write_text("\n".join(lines), encoding="utf-8")
    subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
                    "-c:a", "libmp3lame", "-b:a", "64k", str(out_path)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        from mutagen.mp3 import MP3
        return float(MP3(str(out_path)).info.length)
    except Exception:
        return sum(seg_duration(s) for s in seg_paths) + PAUSE_SEC * (len(seg_paths) - 1)


async def build_episode_mp3(turns, out_path: Path) -> int:
    """各セリフを話者の声で合成し、滑らかに結合した1本のmp3にする。総再生秒を返す。"""
    tmp_dir = EP_DIR / "_segments"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    seg_paths = []
    total = 0.0
    for i, (speaker, text) in enumerate(turns):
        voice = SPEAKERS.get(speaker, SPEAKERS[DEFAULT_SPEAKER])
        seg = tmp_dir / f"{i:03d}.mp3"
        await synth_segment(text, voice, seg)
        seg_paths.append(seg)
        total += seg_duration(seg, text)
    if FFMPEG:
        total = _stitch_with_ffmpeg(seg_paths, tmp_dir, out_path)   # 滑らかに結合
    else:
        with open(out_path, "wb") as out:                          # 予備：単純連結
            for seg in seg_paths:
                out.write(seg.read_bytes())
    shutil.rmtree(tmp_dir, ignore_errors=True)
    return int(total)


def fmt_duration(sec: int) -> str:
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def load_meta() -> list:
    return json.loads(META.read_text(encoding="utf-8")) if META.exists() else []


def save_meta(eps: list):
    META.write_text(json.dumps(eps, ensure_ascii=False, indent=2), encoding="utf-8")


def render_feed(eps: list) -> str:
    now = format_datetime(datetime.now(JST))
    items = "\n".join(f"""    <item>
      <title>{html.escape(e['title'])}</title>
      <description>{html.escape(e['title'])}</description>
      <pubDate>{e['pubDate']}</pubDate>
      <guid isPermaLink="false">{e['file']}</guid>
      <enclosure url="{BASE_URL}/episodes/{e['file']}" length="{e['bytes']}" type="audio/mpeg"/>
      <itunes:duration>{e['duration']}</itunes:duration>
      <itunes:explicit>false</itunes:explicit>
    </item>""" for e in eps)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>{html.escape(PODCAST_TITLE)}</title>
    <link>{BASE_URL}/</link>
    <language>{PODCAST_LANG}</language>
    <description>{html.escape(PODCAST_DESC)}</description>
    <lastBuildDate>{now}</lastBuildDate>
    <itunes:author>{html.escape(PODCAST_AUTHOR)}</itunes:author>
    <itunes:summary>{html.escape(PODCAST_DESC)}</itunes:summary>
    <itunes:explicit>false</itunes:explicit>
    <itunes:category text="Music"/>
    <itunes:image href="{BASE_URL}/cover.jpg"/>
    <image>
      <url>{BASE_URL}/cover.jpg</url>
      <title>{html.escape(PODCAST_TITLE)}</title>
      <link>{BASE_URL}/</link>
    </image>
{items}
  </channel>
</rss>
"""


def publish(label: str):
    """public/ を gh-pages へ単一コミットで force-push（履歴フラット）。"""
    remote = subprocess.check_output(
        ["git", "-C", str(REPO_DIR), "remote", "get-url", "origin"]
    ).decode().strip()
    (PUBLIC_DIR / ".nojekyll").write_text("", encoding="utf-8")
    shutil.rmtree(PUBLIC_DIR / ".git", ignore_errors=True)

    def g(*args):
        subprocess.run(["git", "-C", str(PUBLIC_DIR), *args], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    g("init", "-q", "-b", "gh-pages")
    g("add", "-A")
    g("-c", "user.email=miopapa0424@gmail.com", "-c", "user.name=yasuhiro",
      "commit", "-q", "-m", f"episode {label}")
    g("push", "-q", "-f", remote, "gh-pages")


def main():
    if len(sys.argv) < 2:
        print("使い方: python generate_podcast_dialogue.py today_dialogue.txt [slug]", file=sys.stderr)
        sys.exit(1)
    raw = Path(sys.argv[1]).read_text(encoding="utf-8")
    slug = sys.argv[2] if len(sys.argv) > 2 else "ep"
    title, turns = parse_dialogue(raw)
    if not turns:
        print("台本が空です（話者ラベル付きの行がありません）。", file=sys.stderr)
        sys.exit(1)

    EP_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(JST)
    date_iso = now.strftime("%Y-%m-%d")
    fname = f"{date_iso}-{slug}.mp3"
    out_path = EP_DIR / fname
    ep_title = (title or PODCAST_TITLE) + "｜聴く詩考"

    total_sec = asyncio.run(build_episode_mp3(turns, out_path))

    eps = [e for e in load_meta() if e["file"] != fname]
    eps.insert(0, {
        "title": ep_title,
        "file": fname,
        "pubDate": format_datetime(now),
        "bytes": out_path.stat().st_size,
        "duration": fmt_duration(total_sec),
    })
    keep, drop = eps[:KEEP_N], eps[KEEP_N:]
    for e in drop:
        (EP_DIR / e["file"]).unlink(missing_ok=True)
    eps = keep

    save_meta(eps)
    (PUBLIC_DIR / "feed.xml").write_text(render_feed(eps), encoding="utf-8")
    publish(f"{date_iso} {slug}")
    print(f"公開しました: {BASE_URL}/episodes/{fname}（{fmt_duration(total_sec)}）")


if __name__ == "__main__":
    main()
