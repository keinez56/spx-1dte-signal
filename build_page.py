"""把 output/backtest.json 和 output/signal.json 嵌進 template.html。

輸出：
- output/dashboard.html：給 Claude Artifact 發布（發布時會自動包上 <html>/<head>）
- site/index.html：給 GitHub Pages 的完整網頁
"""
from common import OUT, ROOT

html = (ROOT / "template.html").read_text(encoding="utf-8")
bt = (OUT / "backtest.json").read_text(encoding="utf-8")
sg = (OUT / "signal.json").read_text(encoding="utf-8")
html = html.replace("/*__BACKTEST__*/null", bt.replace("</", "<\\/")).replace("/*__SIGNAL__*/null", sg.replace("</", "<\\/"))
(OUT / "dashboard.html").write_text(html, encoding="utf-8")

head = ('<!doctype html>\n<html lang="zh-Hant">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        '<meta name="robots" content="noindex, nofollow">\n'
        '<style>:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}'
        'body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style>\n</head>\n<body>\n')
site = ROOT / "site"
site.mkdir(exist_ok=True)
(site / "index.html").write_text(head + html + "\n</body>\n</html>\n", encoding="utf-8")
(site / "signal.json").write_text(sg, encoding="utf-8")
print("wrote", OUT / "dashboard.html", "and", site / "index.html", len(html) // 1024, "KB")
