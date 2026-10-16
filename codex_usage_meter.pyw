# Codex Usage Meter: Codex の使用量（週間・5時間など）を、画面の端に小さな窓で常時表示する。
#
# Claude Code の usage-meter Mod（~/.claude/local-mods/usage-meter）の Codex 版。
# Codex アプリには画面に部品を足す仕組みが無いため、独立した最前面の小窓として動く。
#
# 1行の書式（Claude Code 版と同じ）：
#   週間  ████░░░░░░░░   7%  経過 56%  リセットまで 3日10時間
# 「経過」＝前回リセットから次のリセットまでの期間のうち、いま何%進んだか。
#   使用% が 経過% より小さければ、時間の進みより使用が少ない（余裕あり）。
#
# 数字は `codex app-server` の account/rateLimits/read（会話していなくても最新）。
# app-server は1本だけ起動して持ち続け、60秒ごとに問い合わせる。落ちたら次の回で起動し直す。
# 枠は返ってきたものだけ表示する（契約によっては週間だけで、5時間枠は返ってこない）。
# Windows と Mac で動く（Mac は未確認）。自動起動の設定は install.py。
#
# リセットの合図（0.2.0）：合図があるときだけ3行目を出す。
#   ● ﾘｾｯﾄ予兆（紫）  … 元Codexリセット予報士 ついはじめ氏（@hajimetwi3）の予想・期待のメモ
#   ● ﾘｾｯﾄ予告（水色）… OpenAI の Tibo 氏（@thsottiaux）か OpenAI 公式の予告
#   ● ﾘｾｯﾄ実施済（緑）… Tibo 氏か OpenAI 公式が実施を告げた投稿（告知から12時間）
#   それぞれ投稿の時刻と一文を並べる。X の公開投稿を10分ごとに読み、言い回しで見分ける。
#   投稿は非公式の中継サービス FxTwitter（api.fxtwitter.com）で読む。止まったら3行目を出さないだけ。
#   3行目を押すと元の投稿を開き、マウスを乗せると直近の根拠を出す。
#
# 起動・隠す・終了（0.2.4）：
#   Windows … ログイン時に自動起動。スタートメニューの「Codex 使用量メーター」からも起動できる（install.py が登録）。
#             タスクトレイ（画面右下の通知領域）にアイコンが出て、押すと隠す／表示。アイコンの量と色は週間の使用%。
#   Mac     … ログイン時に自動起動。アプリケーションの「Codex 使用量メーター」からも起動できる（install.py が作る）。
#             隠したら Dock のアイコンを押すと出る。右クリックは2本指クリックか Control＋クリック。
#   2つ目を起動したときは立ち上げず、動いている方を表に出す。
#
# 操作：ドラッグで移動（位置は保存）／右クリックで「今すぐ更新」「使用量ページを開く」「X を今すぐ読み直す」「常に手前に表示」「終了」。
# 試験用：--snapshot <png> で描画して画像に保存し、すぐ終了する。

import glob
import json
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser

REFRESH_MS = 60_000
BAR = 18
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state.json")
LOG = os.path.join(HERE, "meter.log")

BG = "#1e1f24"
FG = "#e6e6e6"
DIM = "#8a8d96"
GREEN = "#4cc38a"
YELLOW = "#e5b73b"
RED = "#e5534b"
LINK = "#6cb6ff"

# 使用量ページ。Codex アプリの「設定 > 使用状況と請求」は codex:// のリンクで直接開けない
# （2026-10 の版でリンクから開ける設定は browser-use・connections など7つだけ）ため、
# アプリが中で使っているのと同じウェブ版のページを既定のブラウザで開く。
USAGE_URL = "https://chatgpt.com/codex/settings/usage"
IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
SANS = "Yu Gothic UI" if IS_WIN else ("Hiragino Sans" if IS_MAC else "Noto Sans CJK JP")
MONO_NAME = "Consolas" if IS_WIN else ("Menlo" if IS_MAC else "DejaVu Sans Mono")
SIZE = 10 if IS_WIN else 13  # Mac は同じ数字だと小さく見える
FONT = (SANS, SIZE)
MONO = (MONO_NAME, SIZE)


def log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


# ---- リセットの合図（X の投稿） ----
VERSION = "0.2.4"
AUTHOR = "野村勇樹（生成AI共創道場）"
REPO_URL = "https://github.com/aikworks/codex-usage-meter"
# 見るアカウントと役割。official＝予告・実施済の根拠にする（Tibo 氏と OpenAI 公式）。
# omen＝予兆だけの根拠にする（元Codexリセット予報士 ついはじめ氏の予想・期待のメモ）。
# Codex 単独の公式アカウントは 2026-10-16 時点で確認できなかった（似た名前は未認証）。見つかったらここに足す。
WATCH = (
    ("thsottiaux", "Tibo", "official"),
    ("OpenAI", "OpenAI", "official"),
    ("OpenAIDevs", "OpenAI Developers", "official"),
    ("hajimetwi3", "ついはじめ", "omen"),
)
NAMES = {u: n for u, n, _ in WATCH}
ROLES = {u: r for u, _, r in WATCH}
X_POLL_MS = 10 * 60_000
SIGNAL_WINDOW = 24 * 3600   # この時間より古い投稿は見ない
DONE_SHOW = 12 * 3600       # 実施済は、実施の告知から12時間だけ出す
TEAL = "#4fc3d9"
LAVENDER = "#b39ddb"
STAGES = {"omen": ("ﾘｾｯﾄ予兆", LAVENDER), "notice": ("ﾘｾｯﾄ予告", TEAL), "done": ("ﾘｾｯﾄ実施済", GREEN)}
# 言い回し（2026-10-10〜15 の実際の投稿から）。新しい言い方が出たらここに足す。
DONE_RE = re.compile(r"it'?s done|all propagated|in the bag|has been reset|limits (have been|were) reset|"
                     r"reset (is |has )?(done|live|complete|landed|rolled out)", re.I)
NOTICE_RE = re.compile(r"day\s*\d+\s*/\s*reset|global reset|"
                       r"reset (landing|by eod|incoming|coming|today|tonight|soon)|resetting", re.I)
NONE_RE = re.compile(r"リセット\s*(ﾅｼ|ナシ|無し|なし)", re.I)
OMEN_RE = re.compile(r"予想|予報|予兆|祈|ｸﾙ|クル|来そう|来る|くる|予定|期待", re.I)
OMEN_NOT_RE = re.compile(r"実施済|ｷﾀ|キタ|検知", re.I)  # 起きた後の報告・振り返りは予兆にしない


def classify(text, user):
    """投稿1件を done／notice／omen／none／None（無関係）に分ける。どれに使えるかはアカウントの役割で決まる。"""
    role = ROLES.get(user)
    if NONE_RE.search(text):
        return "none"
    if role == "official":
        if DONE_RE.search(text):
            return "done"
        if NOTICE_RE.search(text):
            return "notice"
    elif role == "omen":
        if "リセット" in text and OMEN_RE.search(text) and not OMEN_NOT_RE.search(text):
            return "omen"
    return None


def fetch_posts(user, timeout=20):
    import urllib.request
    from email.utils import parsedate_to_datetime
    url = f"https://api.fxtwitter.com/2/profile/{user}/statuses?count=20"
    req = urllib.request.Request(url, headers={"User-Agent": f"codex-usage-meter/{VERSION}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    posts = []
    for p in data.get("results") or []:
        ts = p.get("created_timestamp")
        if not ts and p.get("created_at"):
            try:  # "Thu Oct 15 04:55:51 +0000 2026" 形式
                parts = p["created_at"].split()
                ts = parsedate_to_datetime(" ".join(parts[:4] + [parts[5], parts[4]])).timestamp()
            except (ValueError, IndexError):
                ts = None
        if ts:
            posts.append({"ts": float(ts), "text": p.get("text", ""), "url": p.get("url", ""), "user": user})
    return posts


def signal_state(posts, now):
    """合図を決める。戻り値 (kind, event, events)。
    kind は "done"／"notice"／"omen"／None、event は表示する根拠の投稿、events は新しい順の根拠一覧。"""
    events = []
    for p in posts:
        if now - SIGNAL_WINDOW <= p["ts"] <= now + 600:
            k = classify(p["text"], p["user"])
            if k:
                events.append(dict(p, kind=k))
    events.sort(key=lambda e: -e["ts"])

    def last(kind):
        return next((e for e in events if e["kind"] == kind), None)

    done, notice, omen, none = last("done"), last("notice"), last("omen"), last("none")
    t = lambda e: e["ts"] if e else 0  # noqa: E731
    if done and t(done) >= max(t(notice), t(omen)):
        # 実施済は「予告の後に最初に出た実施の告知」の時刻で出す（後の振り返り投稿で延ばさない）。
        first = min((e for e in events if e["kind"] == "done" and e["ts"] >= t(notice)), key=lambda e: e["ts"])
        # 12時間たつか、「今日はリセット無し」が後から出たら消す
        if now - first["ts"] <= DONE_SHOW and t(none) < first["ts"]:
            return "done", first, events
        return None, None, events
    if notice and t(notice) > t(done) and t(notice) > t(none):
        return "notice", notice, events
    if omen and t(omen) > max(t(done), t(none)):
        return "omen", omen, events
    return None, None, events


# ---- 前回のリセット（蓄積） ----
# resets.json に、確かめたリセットを積み上げていく（消さない）。拾う元：
#   detect …ついはじめ氏の「検知時刻 10/15 15:13 JST」など時刻つきの報告（いちばん正確）
#   local  …このパソコンで、使用%の大きな低下や次のリセット予定の跳びから判断（ついはじめ氏と同じやり方）
#   official … Tibo 氏・OpenAI 公式の実施の告知（告知した時刻）
RESETS = os.path.join(HERE, "resets.json")
SOURCE_RANK = {"detect": 0, "local": 1, "official": 2}
SAME_RESET = 3 * 3600  # 3時間以内の記録は同じリセットとみなしてまとめる
JST = None
DETECT_RE = re.compile(r"(?:検知時刻|日本時間)\D{0,4}(\d{1,2})/(\d{1,2})\D{0,6}?(\d{1,2}):(\d{2})")


def _jst():
    global JST
    if JST is None:
        from datetime import timedelta, timezone
        JST = timezone(timedelta(hours=9))
    return JST


def resets_from_posts(posts):
    """投稿からリセットの記録を拾う（ついはじめ氏の時刻つきの報告と、公式の実施の告知）。"""
    from datetime import datetime
    found = []
    for p in posts:
        text = p["text"]
        if p["user"] == "hajimetwi3" and ("検知時刻" in text or "リセット実施済" in text):
            m = DETECT_RE.search(text)
            if m:
                year = datetime.fromtimestamp(p["ts"], _jst()).year
                at = datetime(year, int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)),
                              tzinfo=_jst()).timestamp()
                if at <= p["ts"] + 600:  # 投稿より後の時刻（予定など）は記録にしない
                    found.append({"at": at, "source": "detect", "by": "ついはじめ氏の検知", "url": p["url"]})
        elif ROLES.get(p["user"]) == "official" and classify(text, p["user"]) == "done":
            found.append({"at": p["ts"], "source": "official", "by": f"{NAMES[p['user']]}の告知", "url": p["url"]})
    return found


def merge_resets(history, new):
    """同じリセット（3時間以内）は1件にまとめ、時刻は 検知 → このパソコン → 公式 の順で採る。"""
    out = [dict(h) for h in history]
    for n in new:
        same = next((h for h in out if abs(h["at"] - n["at"]) <= SAME_RESET), None)
        if same is None:
            out.append(dict(n, sources=[n["by"]]))
            continue
        srcs = same.setdefault("sources", [same["by"]])
        if n["by"] not in srcs:
            srcs.append(n["by"])
        if SOURCE_RANK[n["source"]] < SOURCE_RANK[same["source"]]:
            same.update(at=n["at"], source=n["source"], by=n["by"], url=n.get("url", ""))
    out.sort(key=lambda h: h["at"])
    return out


def load_resets():
    try:
        with open(RESETS, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("resets", []), data.get("last_seen")
    except (OSError, ValueError):
        return [], None


def save_resets(resets, last_seen):
    try:
        with open(RESETS, "w", encoding="utf-8") as f:
            json.dump({"resets": resets, "last_seen": last_seen}, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def detect_local_reset(last_seen, weekly, now):
    """前回見た週間の枠と今を比べ、リセットがあったかを判断する。あれば記録を返す。
    ・同じ枠のまま使用%が10ポイント以上下がった → いまリセットされた
    ・次のリセット予定が1日以上先へ跳んだのに、予定の時刻はまだ来ていなかった → 予定より早いリセット
    （予定どおりの時刻を過ぎた後の切り替わりは、ふだんの週の切り替わりなので数えない）"""
    if not last_seen or not weekly:
        return None
    before_used, before_reset, seen_at = last_seen["used"], last_seen["resetsAt"], last_seen["at"]
    used, resets_at = float(weekly.get("usedPercent", 0)), weekly.get("resetsAt") or 0
    jumped = resets_at - before_reset >= 24 * 3600
    early = jumped and now < before_reset
    dropped = before_used - used >= 10
    if early or (dropped and not jumped):
        # 前回見た時刻からいままでの間に起きた。間が10分以内ならいまの時刻、それより長ければ間の真ん中を目安に
        at = now if now - seen_at <= 15 * 60 else (seen_at + now) / 2
        note = "" if now - seen_at <= 15 * 60 else "（パソコンを止めていた間。時刻は目安）"
        return {"at": at, "source": "local", "by": "このパソコンで検知" + note, "url": ""}
    return None


def ago(sec):
    sec = max(0, sec)
    if sec >= 86400:
        return f"{int(sec // 86400)}日{int(sec % 86400 // 3600)}時間"
    if sec >= 3600:
        return f"{int(sec // 3600)}時間"
    return f"{int(sec // 60)}分"


def find_codex():
    # 環境変数 CODEX_BIN で指定されていればそれを使う。
    if os.environ.get("CODEX_BIN") and os.path.exists(os.environ["CODEX_BIN"]):
        return os.environ["CODEX_BIN"]
    if IS_WIN:
        # Codex アプリ同梱の codex.exe を優先（更新でフォルダ名が変わるので新しいもの）。無ければ npm の CLI 版。
        local = os.environ.get("LOCALAPPDATA", "")
        app = glob.glob(os.path.join(local, "OpenAI", "Codex", "bin", "*", "codex.exe"))
        if app:
            return max(app, key=os.path.getmtime)
        appdata = os.environ.get("APPDATA", "")
        hits = glob.glob(os.path.join(appdata, "npm", "node_modules", "@openai", "codex", "node_modules",
                                      "@openai", "codex-win32-*", "vendor", "*", "bin", "codex.exe"))
        if hits:
            return hits[0]
    if IS_MAC:
        # Codex アプリの中の codex（置き場所は版で変わりうるので、アプリの中を探す）
        for root in ("/Applications/Codex.app", os.path.expanduser("~/Applications/Codex.app")):
            hits = [p for p in glob.glob(os.path.join(root, "Contents", "**", "codex"), recursive=True)
                    if os.path.isfile(p) and os.access(p, os.X_OK)]
            if hits:
                return max(hits, key=os.path.getmtime)
    # 最後に PATH 上の codex（npm / Homebrew で入れた CLI）
    import shutil
    for name in ("codex.exe", "codex.cmd", "codex") if IS_WIN else ("codex",):
        p = shutil.which(name)
        if p:
            return p
    for p in ("/opt/homebrew/bin/codex", "/usr/local/bin/codex"):  # ログイン時起動は PATH が短い
        if os.path.exists(p):
            return p
    return None


class AppServer:
    """codex app-server を1本持ち続け、JSON-RPC で使用量を問い合わせる。"""

    def __init__(self):
        self.proc = None
        self.next_id = 1

    def _start(self):
        exe = find_codex()
        if not exe:
            raise RuntimeError("codex が見つからない（Codex アプリか CLI を入れる。場所は CODEX_BIN でも指定できる）")
        self.proc = subprocess.Popen(
            [exe, "app-server"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW if IS_WIN else 0,  # 黒い窓を出さない
        )
        self._call("initialize", {"clientInfo": {"name": "codex-usage-meter", "version": "0.1.0"}})
        self._send({"jsonrpc": "2.0", "method": "initialized"})

    def _send(self, obj):
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def _call(self, method, params=None, timeout=30):
        rid = self.next_id
        self.next_id += 1
        msg = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("app-server が終了した")
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if obj.get("id") == rid:
                if "error" in obj:
                    raise RuntimeError(str(obj["error"]))
                return obj.get("result")
        raise RuntimeError("応答待ちの時間切れ")

    def rate_limits(self):
        if self.proc is None or self.proc.poll() is not None:
            self._start()
        try:
            return self._call("account/rateLimits/read")
        except Exception:
            self.close()
            raise

    def close(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
        self.proc = None


# ---- 二重起動の防止（2つ目は、動いている方に「表に出して」と伝えて終わる） ----
# 待ち受けの番号は決め打ちしない。WSL のミラー方式などが一時的な通信に使う番号の範囲（例 44620〜48715）と
# ぶつかると「もう動いている」と勘違いして起動できなくなるため（2026-10-16 に 47613 で発生）。
# 空いている番号を OS に選ばせ、instance.json に書いておく。2つ目はその番号に送り、届かなければ自分が起動する。
INSTANCE_FILE = os.path.join(HERE, "instance.json")
INSTANCE_COMMANDS = {b"show", b"hide", b"toggle", b"refresh", b"quit"}


def send_command(word):
    """動いているメーターに指示を送る（試験・2つ目の起動用）。届けば True。"""
    import socket
    try:
        with open(INSTANCE_FILE, encoding="utf-8") as f:
            port = int(json.load(f)["port"])
        with socket.create_connection(("127.0.0.1", port), timeout=2) as c:
            c.sendall(word.encode())
        return True
    except (OSError, ValueError, KeyError):
        return False


def claim_single_instance(on_command):
    """1つ目なら待ち受けを始めて True。すでに動いていれば表示を頼んで False。
    待ち受けは 127.0.0.1（このパソコンの中だけ）で、決まった指示（INSTANCE_COMMANDS）しか受けない。"""
    import socket
    if send_command("show"):
        return False
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))  # 空いている番号を OS に選ばせる
    srv.listen(2)
    try:
        with open(INSTANCE_FILE, "w", encoding="utf-8") as f:
            json.dump({"port": srv.getsockname()[1], "pid": os.getpid()}, f)
    except OSError:
        pass  # 書けなくても本体は動かす（二重起動の防止が効かないだけ）

    def serve():
        while True:
            try:
                conn, _ = srv.accept()
                with conn:
                    word = conn.recv(16).strip()
                    if word in INSTANCE_COMMANDS:
                        on_command(word.decode())
            except OSError:
                return

    threading.Thread(target=serve, daemon=True).start()
    return True


# ---- タスクトレイのアイコン（Windows。pystray が無ければ出さないだけ） ----
def _icon_font(size):
    from PIL import ImageFont
    for path in (r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf",
                 "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/Library/Fonts/Arial Bold.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def tray_image(pct, color):
    """週間の使用%の量と色を描き、Codex の「C」を重ねた小さなアイコン。
    「Codex」「Cx」は 16px でつぶれて読めないため、1文字の「C」にした（2026-10-16 に 16/24/32px で見比べ）。"""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, 61, 61), radius=12, fill=BG, outline="#8a8d96", width=3)
    # 量が少なくても色が見えるよう、最低でも高さ12（16px 表示で3px ほど）を塗る
    h = max(12, int(round(46 * max(0, min(100, pct)) / 100)))
    if pct > 0:
        d.rounded_rectangle((10, 56 - h, 53, 56), radius=6, fill=color)
    f = _icon_font(46)
    l, t, r, b = d.textbbox((0, 0), "C", font=f)
    d.text(((64 - (r - l)) / 2 - l, (64 - (b - t)) / 2 - t - 1), "C", font=f, fill="white", stroke_width=4, stroke_fill=BG)
    return img


class Tray:
    def __init__(self, commands):
        import pystray
        self.commands = commands
        put = lambda c: (lambda icon, item: commands.put(c))  # noqa: E731
        menu = pystray.Menu(
            pystray.MenuItem("表示／隠す", put("toggle"), default=True),  # アイコンを押したときもこれ
            pystray.MenuItem("今すぐ更新", put("refresh")),
            pystray.MenuItem("Codex使用量ページを開く", put("usage")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("作者について", put("about")),
            pystray.MenuItem("終了", put("quit")),
        )
        self.icon = pystray.Icon("codex-usage-meter", tray_image(0, DIM), f"Codex 使用量メーター v{VERSION}", menu)
        self.icon.run_detached()

    def update(self, pct, color, title):
        try:
            self.icon.icon = tray_image(pct, color)
            self.icon.title = title[:120]
        except Exception:  # アイコンの更新に失敗しても本体は動かし続ける
            pass

    def stop(self):
        try:
            self.icon.stop()
        except Exception:
            pass


def start_tray(commands):
    if IS_MAC:
        return None  # Mac は Dock のアイコンから出し直す（メニューバー常駐は Tk と同時に動かせないため使わない）
    try:
        return Tray(commands)
    except Exception as ex:
        log(f"タスクトレイのアイコンを出せません（pystray 未導入など）: {ex}")
        return None


def open_usage_page():
    webbrowser.open(USAGE_URL)


def label_for(mins):
    if mins == 10080:
        return "週間"
    if mins == 300:
        return "5時間"
    if mins and mins % 1440 == 0:
        return f"{mins // 1440}日"
    if mins and mins % 60 == 0:
        return f"{mins // 60}時間"
    return f"{mins}分"


def windows_from(result):
    """応答から表示する枠を、長い期間（週間）を先にして並べる。"""
    rl = (result or {}).get("rateLimits") or {}
    out = []
    for key in ("primary", "secondary"):
        w = rl.get(key)
        if w and w.get("windowDurationMins"):
            out.append(w)
    out.sort(key=lambda w: -w["windowDurationMins"])
    return out


def color_for(pct):
    if pct >= 90:
        return RED
    if pct >= 70:
        return YELLOW
    return GREEN


def remaining(resets_at, now):
    if not resets_at:
        return "—"
    sec = resets_at - now
    if sec <= 0:
        return "まもなく"
    mins = int(sec // 60)
    d, h, m = mins // 1440, (mins % 1440) // 60, mins % 60
    if d > 0:
        return f"{d}日{h}時間"
    if h > 0:
        return f"{h}時間{m}分"
    return f"{m}分"


def elapsed_pct(w, now):
    win = w["windowDurationMins"] * 60
    if not w.get("resetsAt"):
        return None
    left = w["resetsAt"] - now
    return max(0, min(100, round((win - left) / win * 100)))


class Meter:
    def __init__(self, root):
        self.root = root
        self.server = AppServer()
        self.result = None
        self.error = None
        self.updated = None
        self.topmost = True
        self.busy = False
        self.posts = []   # X の投稿（合図の根拠）
        self.signal = None
        self.x_posts = {}     # アカウントごとの最後に読めた投稿（読めない回は前回分を使い続ける）
        self.x_fail = {}      # アカウントごとの {"since": 失敗し始めた時刻, "error": 最後のエラー}
        self.x_last_ok = None
        self.x_busy = False
        self.x_retry_pending = False
        self.tip = None
        self.resets, self.last_seen = load_resets()  # 前回のリセットの蓄積と、前回見た週間の枠
        self.hidden = False
        self.tray = None
        import queue
        self.commands = queue.Queue()  # タスクトレイ・2つ目の起動からの指示

        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=BG)
        self.frame = None  # 表示の入れ物。render() のたびに作り直して、古い方と差し替える

        self.menu = tk.Menu(root, tearoff=0)
        # 版はふだん隠しておき、右クリックの先頭に出す（押すと最新版と更新履歴のページを開く）
        self.menu.add_command(label=f"Codex 使用量メーター v{VERSION}", command=lambda: webbrowser.open(REPO_URL))
        # 最終データ取得の時刻（メニューを開くたびに書き換える。押しても何もしない）
        self.menu.add_command(label="", state="disabled")   # 1: 使用量の取得
        self.menu.add_command(label="", state="disabled")   # 2: X の取得
        self.menu.add_separator()
        self.menu.add_command(label="今すぐ更新", command=self.refresh)
        self.menu.add_command(label="Codex使用量ページを開く", command=open_usage_page)
        self.menu.add_command(label="X を今すぐ読み直す", command=self.refresh_x)
        # 常に手前に表示（ほかのウィンドウの下に隠れない）。オンのときはチェックが付く
        self.topmost_var = tk.BooleanVar(value=True)
        self.menu.add_checkbutton(label="常に手前に表示", variable=self.topmost_var, command=self.toggle_top)
        # 隠す：Windows はタスクトレイのアイコン、Mac は Dock のアイコンから出し直す
        self.menu.add_command(label="隠す（Dock のアイコンで再表示）" if IS_MAC else "隠す（タスクトレイのアイコンで再表示）",
                              command=self.hide)
        self.menu.add_separator()
        self.menu.add_command(label="作者について", command=self.show_about)
        self.menu.add_command(label="終了", command=self.quit)

        self._drag = None
        self._load_pos()
        self.render()

    # ---- 位置・操作 ----
    def _load_pos(self):
        try:
            with open(STATE, encoding="utf-8") as f:
                s = json.load(f)
            self.root.geometry(f"+{int(s['x'])}+{int(s['y'])}")
        except (OSError, ValueError, KeyError):
            # 初回は右下（タスクバーの上）。右上だと窓の閉じるボタン等に重なる。
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            self.root.geometry(f"+{sw - 680}+{sh - 200}")

    def _save_pos(self):
        try:
            with open(STATE, "w", encoding="utf-8") as f:
                json.dump({"x": self.root.winfo_x(), "y": self.root.winfo_y()}, f)
        except OSError:
            pass

    def _bind(self, w):
        w.bind("<ButtonPress-1>", self._press)
        w.bind("<B1-Motion>", self._move)
        w.bind("<ButtonRelease-1>", lambda e: self._save_pos())
        self._bind_menu(w)

    def _bind_menu(self, w):
        # 右クリック。Mac の Tk では右ボタンが2番として届くことがあるので、2番と Control＋クリックも受ける
        for seq in (("<Button-2>", "<Button-3>", "<Control-Button-1>") if IS_MAC else ("<Button-3>",)):
            w.bind(seq, self.popup_menu)

    # ---- 隠す・表示（タスクトレイ・Dock から） ----
    def hide(self):
        self._hide_tip()
        self.root.withdraw()
        self.hidden = True
        self._update_tray(windows_from(self.result), time.time())

    def show(self):
        self.root.deiconify()
        self.root.attributes("-topmost", self.topmost_var.get())
        self.root.lift()
        self.hidden = False
        self._update_tray(windows_from(self.result), time.time())

    def toggle_visible(self):
        self.show() if self.hidden else self.hide()

    def _poll_commands(self):
        """タスクトレイや2つ目の起動からの指示を、画面の担当（Tk）の側で実行する。"""
        while not self.commands.empty():
            cmd = self.commands.get_nowait()
            {"show": self.show, "hide": self.hide, "toggle": self.toggle_visible, "refresh": self.refresh,
             "usage": open_usage_page, "about": lambda: (self.show(), self.show_about()), "quit": self.quit}.get(cmd, lambda: None)()
        if self.root.winfo_exists():
            self.root.after(200, self._poll_commands)

    def popup_menu(self, e):
        """右クリックのメニューを開く。版の下の「最終データ取得」をいまの値に書き換えてから出す。"""
        self.refresh_menu_labels()
        self.menu.tk_popup(e.x_root, e.y_root)

    def refresh_menu_labels(self):
        fmt = lambda t: time.strftime("%m/%d %H:%M:%S", time.localtime(t)) if t else "まだ"  # noqa: E731
        self.menu.entryconfigure(1, label=f"　使用量の最終取得：{fmt(self.updated)}" + ("（失敗中）" if self.error else ""))
        self.menu.entryconfigure(2, label=f"　X の最終取得：{fmt(self.x_last_ok)}" + ("（一部失敗）" if self.x_fail else ""))

    def show_about(self):
        """「作者について」：作者・版・ライセンス・参照先のクレジットを小さな窓で出す（閉じるまで出したまま）。"""
        if getattr(self, "about", None) is not None and self.about.winfo_exists():
            self.about.lift()
            return
        win = tk.Toplevel(self.root)
        win.title("作者について")
        win.configure(bg=BG)
        win.attributes("-topmost", True)
        win.resizable(False, False)
        self.about = win
        body = tk.Frame(win, bg=BG, padx=16, pady=12)
        body.pack()
        rows = [
            (f"Codex 使用量メーター v{VERSION}", FG, (SANS, SIZE, "bold"), None),
            (f"作者：{AUTHOR}", FG, (SANS, SIZE - 1), None),
            ("ライセンス：MIT（無保証）", DIM, (SANS, SIZE - 2), None),
            (REPO_URL, LINK, (SANS, SIZE - 2, "underline"), REPO_URL),
            ("", DIM, (SANS, 4), None),
            ("リセットの合図で参照している X のアカウント（敬称略）", DIM, (SANS, SIZE - 2), None),
        ]
        for user, name, role in WATCH:
            rows.append((f"  {name}（@{user}）　{'予告・実施済' if role == 'official' else '予兆'}", DIM, (SANS, SIZE - 2),
                         f"https://x.com/{user}"))
        rows += [
            ("投稿の読み込み：FxTwitter（api.fxtwitter.com、非公式の無料中継サービス）", DIM, (SANS, SIZE - 2), None),
            ("Codex・ChatGPT は OpenAI の製品です。このツールは OpenAI 公式のものではありません。", DIM, (SANS, SIZE - 2), None),
        ]
        for text, color, font, url in rows:
            lbl = tk.Label(body, text=text, fg=color, bg=BG, font=font, anchor="w", justify="left",
                           cursor="hand2" if url else "")
            lbl.pack(anchor="w")
            if url:
                lbl.bind("<Button-1>", lambda e, u=url: webbrowser.open(u))
        tk.Button(body, text="閉じる", command=win.destroy, relief="flat", bg="#2b2d33", fg=FG,
                  activebackground="#3a3d45", activeforeground=FG, padx=12).pack(anchor="e", pady=(10, 0))
        win.update_idletasks()
        x = min(self.root.winfo_rootx(), self.root.winfo_screenwidth() - win.winfo_width() - 8)
        y = max(0, self.root.winfo_rooty() - win.winfo_height() - 8)
        win.geometry(f"+{max(0, x)}+{y}")

    def _press(self, e):
        self._drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def _move(self, e):
        if self._drag:
            self.root.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    def toggle_top(self):
        # メニューのチェック（topmost_var）がすでに切り替わっているので、それに合わせる
        self.topmost = self.topmost_var.get()
        self.root.attributes("-topmost", self.topmost)

    def quit(self):
        self.server.close()
        if self.tray is not None:
            self.tray.stop()
        self.root.destroy()

    # ---- 更新 ----
    def schedule(self):
        self.refresh()
        self.root.after(REFRESH_MS, self.schedule)

    def schedule_x(self):
        self.refresh_x()
        self.root.after(X_POLL_MS, self.schedule_x)

    def refresh_x(self):
        if self.x_busy:
            return
        self.x_busy = True
        threading.Thread(target=self._fetch_x, daemon=True).start()

    def _fetch_x(self):
        """X を読む。自分で直す仕組み：①その場で2回まで読み直す（5秒後・30秒後）、②アカウントごとに切り分けて
        読めた分だけ入れ替える（読めないアカウントは前回分を使い続ける）、③全部読めない間は2分後にも読み直す。"""
        now = time.time()
        for user, _, _ in WATCH:
            err = None
            for wait in (0, 5, 30):
                time.sleep(wait)
                try:
                    self.x_posts[user] = fetch_posts(user)
                    err = None
                    break
                except Exception as ex:  # 中継サービスが止まっても、メーター本体は動かし続ける
                    err = f"{type(ex).__name__}: {ex}"
            if err:
                if user not in self.x_fail:
                    log(f"X の取得失敗 {user}: {err}")
                self.x_fail.setdefault(user, {"since": now})["error"] = err
            else:
                if user in self.x_fail:
                    log(f"X の取得が回復 {user}")
                self.x_fail.pop(user, None)
        if len(self.x_fail) < len(WATCH):
            self.x_last_ok = time.time()
        self.posts = [p for ps in self.x_posts.values() for p in ps]
        self._add_resets(resets_from_posts(self.posts))
        self.x_busy = False
        if len(self.x_fail) == len(WATCH) and not self.x_retry_pending:
            self.x_retry_pending = True
            self.root.after(2 * 60_000, self._retry_x)
        self.root.after(0, self.render)

    def _retry_x(self):
        self.x_retry_pending = False
        self.refresh_x()

    def x_problem(self, now):
        """知らせるべき不調があれば (見出し, 詳細の行) を返す。全部が20分以上、または一部が30分以上読めないとき。"""
        if not self.x_fail:
            return None
        oldest = min(f["since"] for f in self.x_fail.values())
        all_down = len(self.x_fail) == len(WATCH)
        if now - oldest < (20 if all_down else 30) * 60:
            return None
        since = time.strftime("%H:%M", time.localtime(oldest))
        head = (f"⚠ X の読み込みが止まっています（{since}から・自動で再試行中）" if all_down else
                f"⚠ 一部のアカウントが読めません（{since}から）")
        lines = [head]
        for user, f in self.x_fail.items():
            lines.append(f"@{user}：{time.strftime('%m/%d %H:%M', time.localtime(f['since']))}から　{f['error'][:70]}")
        if self.x_last_ok:
            lines.append("最後に読めた時刻：" + time.strftime("%m/%d %H:%M", time.localtime(self.x_last_ok)))
        lines.append("10分ごと（全部止まっている間は2分後にも）自動で読み直します。押すと今すぐ読み直します。")
        lines.append("長く続くときは、中継サービス（FxTwitter）の停止・仕様変更か、アカウント名の変更が考えられます。")
        return head, lines


    def _add_resets(self, found):
        merged = merge_resets(self.resets, found)
        if merged != self.resets:
            for r in found:
                if not any(abs(h["at"] - r["at"]) <= SAME_RESET for h in self.resets):
                    log("リセットを記録: " + time.strftime("%m/%d %H:%M", time.localtime(r["at"])) + " " + r["by"])
            self.resets = merged
            save_resets(self.resets, self.last_seen)

    def _check_local_reset(self, res):
        """このパソコンで見た週間の枠を前回と比べて、リセットを判断する（結果は蓄積へ）。"""
        weekly = next((w for w in windows_from(res) if w["windowDurationMins"] == 10080), None)
        if not weekly:
            return
        now = time.time()
        found = detect_local_reset(self.last_seen, weekly, now)
        self.last_seen = {"used": float(weekly.get("usedPercent", 0)), "resetsAt": weekly.get("resetsAt") or 0, "at": now}
        if found:
            self._add_resets([found])
        else:
            save_resets(self.resets, self.last_seen)

    def last_reset(self, now):
        past = [r for r in self.resets if r["at"] <= now + 60]
        return past[-1] if past else None

    def x_status_lines(self, now):
        """X の読み込み状況（「（今のところ）」に乗せたときに出す）。"""
        fmt = lambda t: time.strftime("%m/%d %H:%M", time.localtime(t))  # noqa: E731
        lines = ["X の最終取得：" + (fmt(self.x_last_ok) + f"（{ago(now - self.x_last_ok)}前）" if self.x_last_ok else "まだ")
                 + "　10分ごとに読み直します"]
        for user, name, _ in WATCH:
            if user in self.x_fail:
                lines.append(f"  @{user}（{name}）：読めていません（{fmt(self.x_fail[user]['since'])}から）")
            else:
                latest = max((p["ts"] for p in self.x_posts.get(user, [])), default=None)
                lines.append(f"  @{user}（{name}）：OK" + (f"　最新の投稿 {fmt(latest)}" if latest else ""))
        return lines

    def reset_lines(self, now, n=5):
        lines = ["これまでのリセット（新しい順）"]
        for r in reversed(self.resets[-n:]):
            lines.append(time.strftime("%m/%d %H:%M  ", time.localtime(r["at"])) + "・".join(r.get("sources", [r["by"]])))
        if len(lines) == 1:
            lines.append("まだ記録がありません（見つけ次第ここにたまります）")
        return lines

    def refresh(self):
        if self.busy:
            return
        self.busy = True
        threading.Thread(target=self._fetch, daemon=True).start()

    def _fetch(self):
        try:
            res = self.server.rate_limits()
            self._check_local_reset(res)
            self.result, self.error, self.updated = res, None, time.time()
        except Exception as ex:  # 前の値のまま表示を続け、印だけ付ける
            self.error = str(ex)
            log("取得失敗: " + self.error)
        self.busy = False
        self.root.after(0, self.render)

    # ---- 描画 ----
    def render(self):
        # ちらつき対策（0.2.4）：古い表示を消してから作ると、消えた瞬間に窓が空・縮んで見える。
        # 新しい入れ物を古い表示の上に重ねて作り切ってから、窓の大きさを合わせ、古い方を消す。
        old = self.frame
        self.frame = tk.Frame(self.root, bg=BG, padx=10, pady=6)
        self._build()
        self.frame.update_idletasks()
        w, h = self.frame.winfo_reqwidth(), self.frame.winfo_reqheight()
        self.frame.place(x=0, y=0)
        # 位置は窓に指定済みの値を使う（起動直後はまだ画面に出ておらず winfo_x が 0 のため）
        m = re.match(r"\d+x\d+([+-]-?\d+)([+-]-?\d+)", self.root.geometry())
        pos = f"{m.group(1)}{m.group(2)}" if m else ""
        self.root.geometry(f"{w}x{h}{pos}")
        if old is not None:
            old.destroy()

    def _build(self):
        now = time.time()
        wins = windows_from(self.result)
        if not wins:
            text = "Codex 使用量  取得中…" if not self.error else "Codex 使用量  取得できません（右クリックで再試行）"
            self._row([(text, DIM, FONT)])
        for i, w in enumerate(wins):
            pct = max(0, min(100, float(w.get("usedPercent", 0))))
            col = color_for(pct)
            filled = round(pct / 100 * BAR)
            el = elapsed_pct(w, now)
            parts = [
                (label_for(w["windowDurationMins"]), FG, (SANS, SIZE, "bold" if i == 0 else "normal"), 6),
                ("█" * filled, col, MONO),
                ("░" * (BAR - filled), DIM, MONO),
                (f" {round(pct):>3}%", col, (MONO_NAME, SIZE, "bold")),
            ]
            if el is not None:
                parts += [("  経過 ", DIM, FONT), (f"{el}%", FG, FONT)]
            parts.append((f"  リセットまで {remaining(w.get('resetsAt'), now)}", DIM, FONT))
            self._row(parts)
        rl = (self.result or {}).get("rateLimits") or {}
        if rl.get("rateLimitReachedType"):
            self._row([("上限に到達中", RED, FONT)])
        foot = "Codex"
        credits = [c for c in ((self.result or {}).get("rateLimitResetCredits") or {}).get("credits") or []
                   if c.get("status") == "available"]
        if credits:  # Claude Code 版 0.3.0 の「回復アイテム」に当たる、無料リセットの残り
            soon = min(c.get("expiresAt") or 0 for c in credits)
            foot += f"  リセット券 {len(credits)}枚"
            if soon:
                # 期限は秒単位の時刻で届く。このパソコンの標準時刻（日本なら日本時間）で日時まで出す
                foot += time.strftime("（最短 %m/%d %H:%M まで）", time.localtime(soon))
        if self.updated:
            foot += time.strftime("  %H:%M 更新", time.localtime(self.updated))
        if self.error and self.result:
            foot += "  ⚠ 更新失敗（前の値）"
        row = self._row([(foot, DIM, (SANS, SIZE - 2))])
        # 「Codex」の文字にマウスを乗せたときだけ版を出す
        first = row.winfo_children()[0]
        info = f"Codex 使用量メーター v{VERSION}\n最新版と更新履歴：{REPO_URL}"
        first.bind("<Enter>", lambda e: self._show_tip(info, e.x_root, e.y_root), add="+")
        first.bind("<Leave>", lambda e: self._hide_tip(), add="+")
        self._signal_row()
        self._problem_row()
        self._update_tray(wins, now)
        # 「使用量ページを開く」はドラッグ用の割り当てを付けず、押したらブラウザで開くだけにする。
        # ふだんは周りの文字と同じ色に下線だけ。マウスを乗せている間だけ青系にする。
        link = tk.Label(row, text="Codex使用量ページを開く", fg=DIM, bg=BG, font=(SANS, SIZE - 2, "underline"),
                        bd=0, padx=0, pady=0, cursor="hand2")
        link.pack(side="left", padx=(12, 0))
        link.bind("<Enter>", lambda e: link.configure(fg=LINK))
        link.bind("<Leave>", lambda e: link.configure(fg=DIM))
        link.bind("<Button-1>", lambda e: open_usage_page())
        self.link = link
        self._bind_menu(link)
        self._bind(self.frame)

    def _signal_row(self):
        """合図があるときだけ3行目を出す：「● ﾘｾｯﾄ予告  10/15 13:55  Tibo：Day 10/ Reset」。
        押すと元の投稿を開き、マウスを乗せると直近の根拠を3件まで出す。"""
        now = time.time()
        kind, ev, events = signal_state(self.posts, now)
        self.signal = kind
        small = (SANS, SIZE - 2)
        last = self.last_reset(now)
        # この行は余白があるので全角で書く（合図の短い表示「ﾘｾｯﾄ予告」などは半角のまま）
        last_text = (time.strftime("前回のリセット %m/%d %H:%M", time.localtime(last["at"])) + f"（{ago(now - last['at'])}前）"
                     if last else "前回のリセット 記録なし")
        if not kind:
            # 合図が無いときも、予兆が無いことと前回のリセットを出す（X を一度も読めていないときは出さない）
            if not self.x_posts and not self.resets:
                return
            row = tk.Frame(self.frame, bg=BG)
            row.pack(anchor="w")
            self._bind(row)
            # 「（今のところ）」に乗せると X の読み込み状況、「前回のリセット」に乗せるとリセットの履歴
            for text, tip in (("○ リセット予兆なし", None), ("（今のところ）", self.x_status_lines(now)),
                              ("　" + last_text, self.reset_lines(now))):
                lbl = tk.Label(row, text=text, fg=DIM, bg=BG, font=small, bd=0, padx=0, pady=0)
                lbl.pack(side="left")
                self._bind(lbl)
                if tip:
                    lbl.bind("<Enter>", lambda e, t=tip: self._show_tip("\n".join(t), e.x_root, e.y_root), add="+")
                    lbl.bind("<Leave>", lambda e: self._hide_tip(), add="+")
            return
        label, color = STAGES[kind]
        row = tk.Frame(self.frame, bg=BG, cursor="hand2")
        row.pack(anchor="w")
        text = ev["text"].replace("\n", " ").strip()
        parts = [
            ("● " + label, color, (SANS, SIZE - 2, "bold")),
            (time.strftime("  %m/%d %H:%M  ", time.localtime(ev["ts"])), FG, small),
            (f"{NAMES.get(ev['user'], ev['user'])}：{text[:34]}{'…' if len(text) > 34 else ''}", DIM, small),
        ]
        names = {"omen": "予兆", "notice": "予告", "done": "実施済", "none": "無し"}
        lines = [f"{label}（{time.strftime('%m/%d %H:%M', time.localtime(ev['ts']))}）"]
        for e in events[:3]:
            lines.append(time.strftime("%m/%d %H:%M ", time.localtime(e["ts"])) +
                         f"[{names[e['kind']]}] {NAMES.get(e['user'], e['user'])}：" + e["text"].replace("\n", " ")[:60])
        lines.append(last_text)
        lines.append("押すと元の投稿を開きます")
        widgets = [row]
        for t, c, f in parts:
            lbl = tk.Label(row, text=t, fg=c, bg=BG, font=f, bd=0, padx=0, pady=0, cursor="hand2")
            lbl.pack(side="left")
            widgets.append(lbl)
        for w in widgets:  # ドラッグ用の割り当ては付けない（押したら投稿を開くだけ）
            w.bind("<Enter>", lambda e: self._show_tip("\n".join(lines), e.x_root, e.y_root))
            w.bind("<Leave>", lambda e: self._hide_tip())
            w.bind("<Button-1>", lambda e, u=ev["url"]: webbrowser.open(u))
            self._bind_menu(w)

    def _update_tray(self, wins, now):
        """タスクトレイのアイコンを、週間の使用%の量と色に合わせる。マウスを乗せたときの文も更新する。"""
        if self.tray is None:
            return
        if not wins:
            self.tray.update(0, DIM, f"Codex 使用量メーター v{VERSION}（取得中）")
            return
        w = wins[0]
        pct = max(0, min(100, float(w.get("usedPercent", 0))))
        title = (f"Codex {label_for(w['windowDurationMins'])} {round(pct)}%・リセットまで {remaining(w.get('resetsAt'), now)}"
                 + ("（隠れています。押すと表示）" if self.hidden else ""))
        self.tray.update(pct, color_for(pct), title)

    def _problem_row(self):
        """X の読み込みの不調を黄色の小さな1行で知らせる。押すと今すぐ読み直す。"""
        problem = self.x_problem(time.time())
        if not problem:
            return
        head, lines = problem
        row = tk.Frame(self.frame, bg=BG, cursor="hand2")
        row.pack(anchor="w")
        lbl = tk.Label(row, text=head, fg=YELLOW, bg=BG, font=(SANS, SIZE - 2), bd=0, padx=0, pady=0, cursor="hand2")
        lbl.pack(side="left")
        for w in (row, lbl):
            w.bind("<Enter>", lambda e: self._show_tip("\n".join(lines), e.x_root, e.y_root))
            w.bind("<Leave>", lambda e: self._hide_tip())
            w.bind("<Button-1>", lambda e: self.refresh_x())
            self._bind_menu(w)

    def _show_tip(self, text, x, y):
        self._hide_tip()
        tip = tk.Toplevel(self.root)
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        tk.Label(tip, text=text, fg=FG, bg="#2b2d33", font=(SANS, SIZE - 2), justify="left",
                 padx=8, pady=6).pack()
        tip.update_idletasks()
        # メーターの左端にそろえて上に出す。画面の右端・上端からははみ出さない
        left = min(self.root.winfo_rootx(), self.root.winfo_screenwidth() - tip.winfo_width())
        top = self.root.winfo_rooty() - tip.winfo_height() - 4
        tip.geometry(f"+{max(0, left)}+{max(0, top)}")
        self.tip = tip

    def _hide_tip(self):
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None

    def _row(self, parts):
        row = tk.Frame(self.frame, bg=BG)
        row.pack(anchor="w")
        self._bind(row)
        for text, color, font, *width in parts:
            # 見出しは文字数で幅を固定し、行ごとにバーの開始位置を揃える
            lbl = tk.Label(row, text=text, fg=color, bg=BG, font=font, bd=0, padx=0, pady=0,
                           width=width[0] if width else 0, anchor="w")
            lbl.pack(side="left")
            self._bind(lbl)
        return row


def main():
    snapshot = None
    if "--snapshot" in sys.argv:
        snapshot = sys.argv[sys.argv.index("--snapshot") + 1]
    try:  # 高解像度画面で文字をにじませず、座標も実画素に揃える
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    import queue
    commands = queue.Queue()
    # すでに動いていれば、そちらを表に出して終わる（スタートメニュー等から2回起動したとき）
    if not snapshot and not claim_single_instance(commands.put):
        return
    root = tk.Tk()
    root.title("Codex Usage Meter")
    meter = Meter(root)
    meter.commands = commands
    if snapshot:
        meter._fetch()
        root.update()
        root.geometry("+40+40")
        root.update()
        time.sleep(0.5)
        root.update()
        from PIL import ImageGrab
        x, y = root.winfo_rootx(), root.winfo_rooty()
        ImageGrab.grab((x, y, x + root.winfo_width(), y + root.winfo_height())).save(snapshot)
        meter.quit()
        return
    meter.tray = start_tray(commands)
    if IS_MAC:
        try:  # Dock のアイコンを押したら、隠していたメーターを出す
            root.createcommand("::tk::mac::ReopenApplication", meter.show)
        except tk.TclError:
            pass
    root.after(200, meter._poll_commands)
    root.after(200, meter.schedule)
    root.after(1500, meter.schedule_x)
    root.mainloop()


if __name__ == "__main__":
    main()
