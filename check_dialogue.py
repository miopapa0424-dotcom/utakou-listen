#!/usr/bin/env python3
"""
「聴く詩考」台本（today_dialogue.txt）の公開前チェッカー（Code担当の機械検査）。
正典 RULES_v4.1（v4.2系）の音声特有・完了条件に対応：
  🔴 歌詞の混入（『…』明示引用 / 〔核N｜lyrics_…〕スロット）
  🔴 「話者」の使用（§4-1）
  🔴 楽曲分節用語の使用（§4-2：Aメロ/サビ 等）
  🔴 末尾固定文（§7-2・一字一句）の欠落、または読み解き手以外が読む
  🟡 未知の話者ラベル / ラベル無し行 / URL・記号の混入

使い方:  python check_dialogue.py today_dialogue.txt
終了コード: 🔴が1件でもあれば 1、無ければ 0。
"""
import re
import sys
from pathlib import Path

ALLOWED = {"聞き手", "読み解き手"}
FIXED = "※本稿はAIによるひとつの解釈です。あなたの聴き方が、別の答えを持っているかもしれません。"
SEG_TERMS = ["Aメロ", "Bメロ", "Cメロ", "サビ前", "サビ後", "サビ頭", "サビ中",
             "サビ終わり", "サビ", "イントロ", "アウトロ", "間奏", "ブリッジ"]

red, yellow = [], []


def main():
    if len(sys.argv) < 2:
        print("使い方: python check_dialogue.py today_dialogue.txt", file=sys.stderr)
        sys.exit(2)
    raw = Path(sys.argv[1]).read_text(encoding="utf-8")
    lines = [l.rstrip("\n") for l in raw.splitlines()]

    turns = []  # (speaker or None, text, lineno)
    cur = None
    for n, line in enumerate(lines, 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = re.match(r"^([^:：]{1,8})[:：]\s*(.*)$", s)
        if m and m.group(1).strip() in ALLOWED:
            cur = m.group(1).strip()
            turns.append([cur, m.group(2).strip(), n])
        elif m and m.group(1).strip() not in ALLOWED and len(m.group(1).strip()) <= 8:
            yellow.append(f"L{n}: 未知の話者ラベル「{m.group(1).strip()}」（許可＝聞き手／読み解き手）")
            cur = None
            turns.append([None, s, n])
        else:
            yellow.append(f"L{n}: 話者ラベルの無い行（直前の話者に連結されます）")
            turns.append([cur, s, n])

    full = "\n".join(t[1] for t in turns)

    # 🔴 歌詞スロット／太字引用（＝記事の歌詞核書式）の混入
    for n, line in enumerate(lines, 1):
        if "〔核" in line or "lyrics_" in line or re.search(r"〔.*lyrics", line):
            red.append(f"L{n}: 〔核…｜lyrics…〕スロットの混入（歌詞）→ 除外する")
        if re.search(r"[「『]\s*\*\*.+?\*\*\s*[」』]", line):
            red.append(f"L{n}: 太字の引用「**…**」（記事の歌詞核書式）→ 歌詞は音声に入れない")
    # 🟡 引用符の中身は曲名・作品名ならOK／歌詞ならNG（人手で一瞥）
    for n, line in enumerate(lines, 1):
        for q in re.findall(r"『([^』]{1,40})』", line):
            yellow.append(f"L{n}: 『{q}』← 曲名・作品名か確認（歌詞ならNG）")
        for q in re.findall(r"「([^」]{13,40})」", line):
            yellow.append(f"L{n}: 「{q}」← 歌詞でないか確認")

    # 🔴 「話者」
    for n, line in enumerate(lines, 1):
        if "話者" in line:
            red.append(f"L{n}: 「話者」が使われています（§4-1）→ 主人公／歌い手／彼・彼女へ")

    # 🔴 分節用語
    for n, line in enumerate(lines, 1):
        for term in SEG_TERMS:
            if term in line:
                red.append(f"L{n}: 分節用語「{term}」（§4-2）→ 言い換える")
                break

    # 🟡 URL・脚注記号
    for n, line in enumerate(lines, 1):
        if re.search(r"https?://", line):
            yellow.append(f"L{n}: URLが入っています（音声に入れない）")

    # 🔴 末尾固定文（§7-2・一字一句・読み解き手）
    if not turns:
        red.append("台本が空です（話者ラベル付きの行がありません）")
    else:
        last_sp, last_tx, last_n = turns[-1]
        if last_tx.strip() != FIXED:
            # 固定文がどこかにあるが末尾でない、または微妙に違う場合も拾う
            if FIXED in full:
                red.append("末尾固定文が最後の発話になっていません（§7-2）→ 最後に置く")
            else:
                red.append(f"末尾固定文（一字一句）がありません（§7-2）→ 末尾に次を置く：{FIXED}")
        elif last_sp != "読み解き手":
            red.append(f"L{last_n}: 末尾固定文は読み解き手が読むこと（現在は{last_sp or '不明'}）")

    # 出力
    print("=== 聴く詩考 台本チェック ===")
    print(f"発話数: {len(turns)}　文字数(本文): {len(full)}　目安: 1,500〜2,200字 / 4〜6分")
    if red:
        print(f"\n🔴 必須修正 {len(red)}件:")
        for r in red:
            print("  -", r)
    if yellow:
        print(f"\n🟡 確認 {len(yellow)}件:")
        for y in yellow:
            print("  -", y)
    if not red and not yellow:
        print("\n🟢 問題は見つかりませんでした。")
    elif not red:
        print("\n🟢 🔴なし（公開可）。🟡は内容を確認してください。")
    print()
    sys.exit(1 if red else 0)


if __name__ == "__main__":
    main()
