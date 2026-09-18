import io, re

H = r"C:\Users\DELLLA~1\invoice-ai-system-v2\app\static\index.html"
s = io.open(H, encoding="utf-8").read()

def bump(proto_pat, ref):
    m = re.search(proto_pat, s)
    if not m:
        print(f"!! pas de ref {ref}")
        return
    old = m.group(1)
    new = str(int(old) + 1)
    after = s[:m.start(1)] + new + s[m.end(1):]
    global s
    s = after
    print(f"{ref}: v{old} -> v{new}")

bump(r'app\.js\?v=(\d+)', "app.js")
bump(r'style\.css\?v=(\d+)', "style.css")

io.open(H, "w", encoding="utf-8", newline="\n").write(s)
print("OK")