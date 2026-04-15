# -*- coding: utf-8 -*-sync_ai_signals.py
import os, time, shutil

SRC = os.environ.get("AI_SIG_SRC")
DST = os.environ.get("AI_SIG_DST")
if not SRC or not DST:
    print("[sync] missing SRC/DST")
    raise SystemExit(1)

os.makedirs(os.path.dirname(DST), exist_ok=True)
print("[sync] {} -> {} (poll=1s)".format(SRC, DST), flush=True)

last_sig = (0, 0)

while True:
    try:
        if os.path.exists(SRC):
            st  = os.stat(SRC)
            sig = (int(st.st_mtime), st.st_size)
            if sig != last_sig:
                tmp = DST + ".tmp"
                shutil.copy2(SRC, tmp)
                os.replace(tmp, DST)  # ذَرّي
                last_sig = sig
                print("[sync] copied", flush=True)
    except Exception as e:
        print("[sync][ERR]", e, flush=True)
    time.sleep(1)




