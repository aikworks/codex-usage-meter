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
# 操作：ドラッグで移動（位置は保存）／右クリックで「今すぐ更新」「最前面の切替」「終了」。
# 試験用：--snapshot <png> で描画して画像に保存し、すぐ終了する。

import glob
import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk

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


def open_usage_page():
    import webbrowser
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

        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=BG)
        self.frame = tk.Frame(root, bg=BG, padx=10, pady=6)
        self.frame.pack()

        self.menu = tk.Menu(root, tearoff=0)
        self.menu.add_command(label="今すぐ更新", command=self.refresh)
        self.menu.add_command(label="使用量ページを開く", command=open_usage_page)
        self.menu.add_command(label="最前面の切替", command=self.toggle_top)
        self.menu.add_separator()
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
        w.bind("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root))

    def _press(self, e):
        self._drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def _move(self, e):
        if self._drag:
            self.root.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    def toggle_top(self):
        self.topmost = not self.topmost
        self.root.attributes("-topmost", self.topmost)

    def quit(self):
        self.server.close()
        self.root.destroy()

    # ---- 更新 ----
    def schedule(self):
        self.refresh()
        self.root.after(REFRESH_MS, self.schedule)

    def refresh(self):
        if self.busy:
            return
        self.busy = True
        threading.Thread(target=self._fetch, daemon=True).start()

    def _fetch(self):
        try:
            res = self.server.rate_limits()
            self.result, self.error, self.updated = res, None, time.time()
        except Exception as ex:  # 前の値のまま表示を続け、印だけ付ける
            self.error = str(ex)
            log("取得失敗: " + self.error)
        self.busy = False
        self.root.after(0, self.render)

    # ---- 描画 ----
    def render(self):
        for c in self.frame.winfo_children():
            c.destroy()
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
        # 「使用量ページを開く」はドラッグ用の割り当てを付けず、押したらブラウザで開くだけにする
        link = tk.Label(row, text="使用量ページを開く", fg=LINK, bg=BG, font=(SANS, SIZE - 2, "underline"),
                        bd=0, padx=0, pady=0, cursor="hand2")
        link.pack(side="left", padx=(12, 0))
        link.bind("<Button-1>", lambda e: open_usage_page())
        link.bind("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root))
        self._bind(self.frame)

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
    root = tk.Tk()
    root.title("Codex Usage Meter")
    meter = Meter(root)
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
    root.after(200, meter.schedule)
    root.mainloop()


if __name__ == "__main__":
    main()
