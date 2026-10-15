# Codex Usage Meter のインストーラ。
#   python install.py            … ログイン時の自動起動を設定し、いま起動する
#   python install.py --uninstall … 自動起動の設定を外す（窓は右クリック→終了で閉じる）
#
# Windows: スタートアップに「Codex Usage Meter.lnk」（pythonw.exe で起動するので黒い窓は出ない）
# Mac    : ~/Library/LaunchAgents/com.aikworks.codex-usage-meter.plist
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


def windows_paths():
    startup = os.path.join(os.environ["APPDATA"], "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pyw):
        sys.exit("pythonw.exe が見つかりません（python.org の Python を入れてください）: " + pyw)
    return os.path.join(startup, NAME + ".lnk"), pyw


def install_windows():
    lnk, pyw = windows_paths()
    # ショートカットは PowerShell の WScript.Shell で作る。パスは環境変数で渡し、命令文に埋め込まない
    # （-Command の後ろの引数は命令文につながってしまい、空白入りのパスが崩れるため）。
    ps = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:CUM_LNK); "
          "$s.TargetPath = $env:CUM_TARGET; $s.Arguments = $env:CUM_ARGS; $s.WorkingDirectory = $env:CUM_DIR; "
          "$s.Description = 'Codex Usage Meter'; $s.Save()")
    env = dict(os.environ, CUM_LNK=lnk, CUM_TARGET=pyw, CUM_ARGS=f'"{METER}"', CUM_DIR=HERE)
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
                   check=True, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    # explorer 経由で開くと、呼び出し元（AIのシェル等）が終わっても窓が残る
    subprocess.run(["explorer.exe", lnk], creationflags=subprocess.CREATE_NO_WINDOW)
    print("自動起動を設定しました:", lnk)


def uninstall_windows():
    lnk, _ = windows_paths()
    if os.path.exists(lnk):
        os.remove(lnk)
        print("自動起動を外しました:", lnk)
    else:
        print("自動起動は設定されていません")


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


def uninstall_mac():
    plist = mac_plist()
    if os.path.exists(plist):
        subprocess.run(["launchctl", "unload", "-w", plist], capture_output=True)
        os.remove(plist)
        print("自動起動を外しました:", plist)
    else:
        print("自動起動は設定されていません")


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
        print("画面の右下に小さな窓が出ます。ドラッグで移動、右クリックで更新・終了ができます。")


if __name__ == "__main__":
    main()
