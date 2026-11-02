"""安装界面静态预览：把 ui.html 渲染成图片（headless Edge）。

用桩数据填好版本号/路径，并按 #progress / #done 切换视图截图。
"""
import base64
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI = os.path.join(ROOT, "packaging", "installer", "ui.html")
LOGO = os.path.join(ROOT, "assets", "icons", "app-logo.png")
OUT = os.path.join(ROOT, "build", "tmp")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

STUB = """
<script>
window.pywebview = {api:{
  info: () => Promise.resolve({version:"0.1.0-beta.7", channel_label:"Beta 公测版",
    build_time:"2026-10-30 09:27", default_dir:"C:\\\\Program Files\\\\CSE Compressor",
    payload_mb:32.0, installed:false}),
  close(){}, minimize(){}, choose_dir(){}, launch(){}, install(){}, confirm_uninstall(){}
}};
window.addEventListener("load", () => {
  window.dispatchEvent(new Event("pywebviewready"));
  const h = location.hash;
  if (h === "#progress") {
    show("v-progress");
    setTxt("p-title","正在安装…");
    window.__progress({stage:"extract", pct:62, msg:"释放 cse-cli.exe"});
  } else if (h === "#done") {
    window.__done({ok:true, exe:"C:\\\\Program Files\\\\CSE Compressor\\\\CSECompressor.exe"});
  } else if (h === "#uninstall") {
    show("v-unconfirm");
    setTxt("u-ver","v0.1.0-beta.7");
    document.querySelector(".chan").textContent = "BETA";
  } else if (h === "#upgrade") {
    document.getElementById("upgNotice").hidden = false;
  }
});
</script>
"""


def build(name, mode="install"):
    html = open(UI, encoding="utf-8").read().replace("__MODE__", mode)
    b64 = base64.b64encode(open(LOGO, "rb").read()).decode()
    html = html.replace('src="app-logo.png"', f'src="data:image/png;base64,{b64}"')
    html = html.replace("</body>", STUB + "</body>")
    path = os.path.join(OUT, f"{name}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def shot(html_path, out_png, hash_=""):
    url = "file:///" + html_path.replace("\\", "/") + hash_
    if os.path.isfile(out_png):
        os.remove(out_png)
    subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                    "--force-device-scale-factor=2",
                    f"--screenshot={out_png}", "--window-size=600,400", url],
                   check=True, capture_output=True, timeout=90)
    print(out_png, os.path.getsize(out_png))


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    inst = build("setup_preview")
    un = build("setup_preview_un", mode="uninstall")
    shot(inst, os.path.join(OUT, "setup-welcome.png"))
    shot(inst, os.path.join(OUT, "setup-progress.png"), "#progress")
    shot(inst, os.path.join(OUT, "setup-done.png"), "#done")
    shot(inst, os.path.join(OUT, "setup-upgrade.png"), "#upgrade")
    shot(un, os.path.join(OUT, "setup-uninstall.png"))
    sys.exit(0)
