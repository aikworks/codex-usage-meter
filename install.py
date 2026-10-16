# Codex Usage Meter のインストーラ。
#   python install.py            … ログイン時の自動起動と、手で起動する入口を作り、いま起動する
#   python install.py --uninstall … 自動起動と入口を外す（窓は右クリック→終了で閉じる）
#
# Windows: スタートアップに「Codex Usage Meter.lnk」（自動起動）、スタートメニューに「Codex 使用量メーター.lnk」（手で起動）。
#          タスクトレイのアイコン用に pystray（0.19.5）を入れる。pythonw.exe で起動するので黒い窓は出ない。
# Mac    : ~/Library/LaunchAgents/com.aikworks.codex-usage-meter.plist（自動起動）、
#          ~/Applications/Codex 使用量メーター.app（手で起動。Launchpad・Spotlight から開ける）。未確認。
# メーター本体（codex_usage_meter.pyw）は、このファイルと同じフォルダに置いたまま使う。

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
METER = os.path.join(HERE, "codex_usage_meter.pyw")
NAME = "Codex Usage Meter"
MAC_LABEL = "com.aikworks.codex-usage-meter"


def check_tk():
    try:
        import tkinter  # noqa: F401
    except ImportError:
        sys.exit("この Python には tkinter（画面部品）がありません。python.org の Python を入れてください"
                 "（Homebrew の Python なら `brew install python-tk`）。")


MENU_NAME = "Codex 使用量メーター"
PYSTRAY = "pystray==0.19.5"


def windows_paths():
    programs = os.path.join(os.environ["APPDATA"], "Microsoft", "Windows", "Start Menu", "Programs")
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pyw):
        sys.exit("pythonw.exe が見つかりません（python.org の Python を入れてください）: " + pyw)
    startup = os.path.join(programs, "Startup", NAME + ".lnk")   # ログイン時に自動起動
    menu = os.path.join(programs, MENU_NAME + ".lnk")            # スタートメニューから手で起動
    return startup, menu, pyw


def make_shortcut(lnk, pyw):
    # ショートカットは PowerShell の WScript.Shell で作る。パスは環境変数で渡し、命令文に埋め込まない
    # （-Command の後ろの引数は命令文につながってしまい、空白入りのパスが崩れるため）。
    ps = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:CUM_LNK); "
          "$s.TargetPath = $env:CUM_TARGET; $s.Arguments = $env:CUM_ARGS; $s.WorkingDirectory = $env:CUM_DIR; "
          "$s.Description = 'Codex Usage Meter'; $s.Save()")
    env = dict(os.environ, CUM_LNK=lnk, CUM_TARGET=pyw, CUM_ARGS=f'"{METER}"', CUM_DIR=HERE)
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
                   check=True, env=env, creationflags=subprocess.CREATE_NO_WINDOW)


def install_pystray():
    """タスクトレイのアイコン用の部品。入らなくてもメーター本体は動く（アイコンが出ないだけ）。"""
    try:
        import pystray  # noqa: F401
        return
    except ImportError:
        pass
    r = subprocess.run([sys.executable, "-m", "pip", "install", "--user", PYSTRAY], capture_output=True, text=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    print("タスクトレイ用の部品（pystray）を入れました" if r.returncode == 0 else
          "タスクトレイ用の部品（pystray）を入れられませんでした。アイコン無しで動きます:\n" + r.stderr[-400:])


def install_windows():
    startup, menu, pyw = windows_paths()
    install_pystray()
    make_shortcut(startup, pyw)
    make_shortcut(menu, pyw)
    # explorer 経由で開くと、呼び出し元（AIのシェル等）が終わっても窓が残る。すでに動いていれば表に出るだけ
    subprocess.run(["explorer.exe", startup], creationflags=subprocess.CREATE_NO_WINDOW)
    print("自動起動を設定しました:", startup)
    print("スタートメニューに登録しました:", menu)


def uninstall_windows():
    startup, menu, _ = windows_paths()
    for path, what in ((startup, "自動起動"), (menu, "スタートメニューの登録")):
        if os.path.exists(path):
            os.remove(path)
            print(f"{what}を外しました:", path)
        else:
            print(f"{what}はありません")


def mac_plist():
    return os.path.expanduser(f"~/Library/LaunchAgents/{MAC_LABEL}.plist")


def install_mac():
    plist = mac_plist()
    os.makedirs(os.path.dirname(plist), exist_ok=True)
    with open(plist, "w", encoding="utf-8") as f:
        f.write(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{MAC_LABEL}</string>
  <key>ProgramArguments</key><array><string>{sys.executable}</string><string>{METER}</string></array>
  <key>WorkingDirectory</key><string>{HERE}</string>
  <key>RunAtLoad</key><true/>
</dict></plist>
""")
    subprocess.run(["launchctl", "unload", plist], capture_output=True)
    subprocess.run(["launchctl", "load", "-w", plist], check=True)
    print("自動起動を設定しました:", plist)
    # 手で起動する入口：~/Applications に小さなアプリを作る（Launchpad・Spotlight から開ける）。
    # 中身は「メーターを裏で起動する」だけの AppleScript。すでに動いていれば、動いている方が表に出る。
    app = mac_app()
    os.makedirs(os.path.dirname(app), exist_ok=True)
    script = f'do shell script quoted form of "{sys.executable}" & " " & quoted form of "{METER}" & " > /dev/null 2>&1 &"'
    r = subprocess.run(["osacompile", "-o", app, "-e", script], capture_output=True, text=True)
    print("アプリケーションに登録しました:" if r.returncode == 0 else "アプリケーションに登録できませんでした:", app)


def mac_app():
    return os.path.expanduser(f"~/Applications/{MENU_NAME}.app")


def uninstall_mac():
    plist = mac_plist()
    if os.path.exists(plist):
        subprocess.run(["launchctl", "unload", "-w", plist], capture_output=True)
        os.remove(plist)
        print("自動起動を外しました:", plist)
    else:
        print("自動起動は設定されていません")
    app = mac_app()
    if os.path.isdir(app):
        import shutil
        shutil.rmtree(app)
        print("アプリケーションの登録を外しました:", app)


def main():
    uninstall = "--uninstall" in sys.argv
    if not os.path.exists(METER):
        sys.exit("codex_usage_meter.pyw が同じフォルダにありません: " + HERE)
    if not uninstall:
        check_tk()
    if sys.platform == "win32":
        (uninstall_windows if uninstall else install_windows)()
    elif sys.platform == "darwin":
        (uninstall_mac if uninstall else install_mac)()
    else:
        sys.exit("自動起動の設定は Windows と Mac だけです。`python3 codex_usage_meter.pyw` で直接起動できます。")
    if not uninstall:
        print("画面の右下に小さな窓が出ます。ドラッグで移動、右クリックで更新・隠す・終了ができます。")
        if sys.platform == "win32":
            print("隠したときは、画面右下のタスクトレイのアイコンを押すと出ます。終了したあとは、スタートメニューで"
                  f"「{MENU_NAME}」を探すと起動できます。")
        else:
            print(f"隠したときは Dock のアイコンを押すと出ます。終了したあとは、Launchpad か Spotlight で「{MENU_NAME}」を開くと起動できます。")


if __name__ == "__main__":
    main()
