#!/usr/bin/env python3
"""zpc60 experiment: does 4-bit ADPCM sound good enough to give an MPC60-style sampler ~12 s?
Sounds (CC0 kit, the user's own from the zp12 video) -> 40 kHz (and 20 kHz) -> 12 bit / ADPCM -> played as the FM-1
would (zero-order hold to 44.1 kHz) -> WAVs + a listening page (out/index.html). SNR against the 16-bit source at
the same rate (the codec's own noise)."""
import json, subprocess, wave
from pathlib import Path
import numpy as np
from scipy.signal import resample_poly

HERE = Path(__file__).parent
OUT = HERE / "out"
KIT = HERE.parent.parent / "zp12sloop/assets/samples-cc0"
OWN = HERE.parent.parent / "video/v12/own"
BIN = HERE / "adpcm"
FS = 44100


def load(p):
    w = wave.open(str(p))
    n, ch, sw, r = w.getnframes(), w.getnchannels(), w.getsampwidth(), w.getframerate()
    raw = w.readframes(n)
    if sw == 2:
        x = np.frombuffer(raw, "<i2").astype(np.float64) / 32768
    else:                                              # 24 bit
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3)
        x = ((b[:, 0].astype(np.int32) | b[:, 1].astype(np.int32) << 8 | b[:, 2].astype(np.int32) << 16) << 8 >> 8) / 8388608
    x = x.reshape(-1, ch).mean(axis=1)
    if r != FS:
        x = resample_poly(x, FS, r)
    return x


def save(p, x, rate=FS):
    y = np.clip(np.round(x * 32767), -32768, 32767).astype("<i2")
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(y.tobytes())


def norm(x, pk=0.97):
    return x * pk / max(1e-9, np.abs(x).max())


def build_break():
    """2 bars of boom bap at 90 BPM, swung 16ths (the MPC's 62 %), from the CC0 kit"""
    k = lambda f: load(KIT / "KIT" / f)
    kick, snare, hat, ohat = k("10_kick_BDrumNew_hit_v5.wav"), k("11_snare_Snare2_HitSN_v7.wav"), k("14_hihat_HitC_v3.wav"), k("15_ohat_HitO.wav")
    six = 60 / 90 / 4 * FS
    x = np.zeros(int(32 * six + FS))
    def hit(s, step, g):
        t = int(step * six + (0.24 * six if step % 2 else 0))   # swing: every 2nd 16th late (62 %)
        x[t:t + len(s)] += s[:len(x) - t] * g
    for b in (0, 16):
        for st in (0, 7, 10):
            hit(kick, b + st, 0.9)
        for st in (4, 12):
            hit(snare, b + st, 0.8)
        for st in range(0, 16, 2):
            hit(hat, b + st, 0.35 if st % 4 else 0.5)
        hit(ohat, b + 14, 0.3)
    return norm(x[:int(32 * six)])


def build_loop():
    """a soul loop: the break, piano chord stabs (C3 + E4 + Gs3: a dark chord), the flute on top"""
    br = build_break()
    p = sum(load(KIT / "PIANO" / f) for f in ("00_C3_m48.wav", "01_Gs3_m56.wav", "02_E4_m64.wav"))
    fl = load(OWN / "flute.wav")
    x = br * 0.8
    six = 60 / 90 / 4 * FS
    for st in (0, 6, 16, 22, 26):
        t = int(st * six)
        n = min(len(p), len(x) - t)
        x[t:t + n] += p[:n] * 0.25
    t = int(8 * six)
    n = min(len(fl), len(x) - t)
    x[t:t + n] += fl[:n] * 0.5
    return norm(x)


SOUNDS = [
    ("break", "Break (2 bars, 90 BPM, swing)", build_break),
    ("loop", "Soul loop (break, piano, flute)", build_loop),
    ("kick", "Kick", lambda: norm(load(KIT / "KIT/10_kick_BDrumNew_hit_v5.wav"))),
    ("snare", "Snare", lambda: norm(load(KIT / "KIT/11_snare_Snare2_HitSN_v7.wav"))),
    ("ohat", "Open hat", lambda: norm(load(KIT / "KIT/15_ohat_HitO.wav"))),
    ("crash", "Crash", lambda: norm(load(KIT / "KIT/18_crash_susCymb2_hit_f1.wav"))),
    ("piano", "Piano C3 (soft tail)", lambda: norm(load(KIT / "PIANO/00_C3_m48.wav"))),
    ("flute", "Flute (your own, video 1.6)", lambda: norm(load(OWN / "flute.wav"))),
]

def q12(x):                                            # 12 bit: -2048..2047, as zp12 stores it
    return np.clip(np.round(x * 2047), -2048, 2047) / 2048


def adpcm(x16, blk, look):
    raw, enc, dec = OUT / "_in.s16", OUT / "_enc.adp", OUT / "_dec.s16"
    x16.astype("<i2").tofile(raw)
    subprocess.run([str(BIN), "look" if look else "enc", str(blk), str(raw), str(enc)], check=True, capture_output=True)
    subprocess.run([str(BIN), "dec", str(blk), str(enc), str(len(x16)), str(dec)], check=True)
    return np.fromfile(dec, "<i2").astype(np.float64) / 32768, enc.stat().st_size


def adpcm5(x16, blk):
    raw, enc, dec = OUT / "_in.s16", OUT / "_enc.adp", OUT / "_dec.s16"
    x16.astype("<i2").tofile(raw)
    subprocess.run([str(BIN), "enc5", str(blk), str(raw), str(enc)], check=True)
    subprocess.run([str(BIN), "dec5", str(blk), str(enc), str(len(x16)), str(dec)], check=True)
    return np.fromfile(dec, "<i2").astype(np.float64) / 32768, enc.stat().st_size


def mulaw(x):                                          # 8-bit mu-law (G.711-style curve, mu 255): a 256-entry table to play
    mu = 255.0
    c = np.round(np.sign(x) * np.log1p(mu * np.abs(x)) / np.log1p(mu) * 127)
    return np.sign(c) * ((1 + mu) ** (np.abs(c) / 127) - 1) / mu


def zoh(x, rate):                                      # what the FM-1 plays: each sample held, at 44.1 kHz
    step = int(rate * 65536 // FS)
    pos = (np.arange(int(len(x) * 65536 // step)) * step) >> 16
    return x[pos[pos < len(x)]]


def snr(ref, y):
    e = ref - y
    return 10 * np.log10((ref ** 2).sum() / max(1e-20, (e ** 2).sum()))


def main():
    OUT.mkdir(exist_ok=True)
    rows = []
    for key, title, make in SOUNDS:
        x = make()
        save(OUT / f"{key}-0-orig.wav", x)
        row = {"key": key, "title": title, "secs": round(len(x) / FS, 2), "v": {}}
        for rate in (40000, 20000):
            s = resample_poly(x, rate // 100, FS // 100)
            s = np.clip(s, -1, 32767 / 32768)
            s16 = np.round(s * 32767).astype(np.int64)
            ref = s16 / 32768
            variants = [("12", q12(s), len(s) * 1.5), ("mu", mulaw(s), len(s)), ("a5", *adpcm5(s16, 64))]
            for blk in (64, 256):
                for look in (False, True):
                    y, nb = adpcm(s16, blk, look)
                    variants.append((f"a{blk}{'L' if look else ''}", y, nb))
            for name, y, nb in variants:
                tag = f"{rate // 1000}k-{name}"
                save(OUT / f"{key}-{tag}.wav", zoh(y, rate))
                row["v"][tag] = {"snr": round(snr(ref, y), 1), "kbs": round(nb / (len(s) / rate) / 1024, 1)}
        rows.append(row)
        print(key, {k: v["snr"] for k, v in row["v"].items()})
    (OUT / "results.json").write_text(json.dumps(rows, indent=1))
    for f in OUT.glob("_*"):
        f.unlink()
    bench = subprocess.run([str(BIN), "bench", "64", "4000000"], capture_output=True, text=True).stdout.strip()
    print(bench)
    page(rows, bench)


def page(rows, bench):
    room = {"free": 96, "banks": 240}
    def secs(kbs, kb): return f"{kb / kbs:.1f} s"
    head = rows[0]["v"]
    times = "".join(f"<tr><td>{n}</td><td>{head[t]['kbs']} KB/s</td><td>{secs(head[t]['kbs'], 96)}</td><td><b>{secs(head[t]['kbs'], 240)}</b></td></tr>"
                    for n, t in (("12 bit, 40 kHz", "40k-12"), ("8 bit mu-law, 40 kHz", "40k-mu"), ("ADPCM 5 bit, 40 kHz", "40k-a5"), ("ADPCM 4 bit, 40 kHz", "40k-a64L"), ("12 bit, 20 kHz", "20k-12"), ("ADPCM 4 bit, 20 kHz", "20k-a64L")))
    body = ""
    for r in rows:
        v = r["v"]
        btn = lambda tag, lab: f'<button data-f="{r["key"]}-{tag}.wav">{lab}<small>{v[tag]["snr"]} dB</small></button>' if tag in v else ""
        body += (f'<section><h2>{r["title"]} <span>{r["secs"]} s</span></h2><div class="b">'
                 f'<button data-f="{r["key"]}-0-orig.wav">Original<small>16 bit 44.1</small></button>'
                 f'{btn("40k-12", "12 bit 40 kHz")}{btn("40k-mu", "8 bit μ-law 40 kHz")}{btn("40k-a5", "ADPCM 5 bit 40 kHz")}{btn("40k-a64L", "ADPCM 4 bit 40 kHz")}{btn("20k-12", "12 bit 20 kHz")}{btn("20k-a64L", "ADPCM 20 kHz")}'
                 f'<button class="blind" data-k="{r["key"]}">? Blind: 12 bit or ADPCM 4 bit</button><span class="ans"></span></div></section>')
    detail = "".join(f"<tr><td>{r['title']}</td>" + "".join(f"<td>{r['v'][t]['snr']}</td>" for t in ("40k-12", "40k-mu", "40k-a5", "40k-a256", "40k-a64L", "20k-12", "20k-a64L")) + "</tr>" for r in rows)
    html = f"""<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>zpc60 ADPCM test</title><style>
:root {{ --bg:#d9d6cc; --panel:#ece9e0; --ink:#262420; --dim:#6d6a62; --red:#c0392b; --line:#bdb9ae; }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:16px/1.5 system-ui, sans-serif; }}
.w {{ max-width:980px; margin:0 auto; padding:24px 16px 60px; }}
h1 {{ font-weight:800; letter-spacing:-.5px; margin:0 0 6px; }} h1 b {{ color:var(--red); }}
p {{ margin:6px 0; }} .dim {{ color:var(--dim); font-size:14px; }}
section {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:12px 14px; margin:12px 0; }}
h2 {{ font-size:17px; margin:0 0 8px; }} h2 span {{ color:var(--dim); font-weight:400; font-size:14px; }}
.b {{ display:flex; flex-wrap:wrap; gap:8px; align-items:center; }}
button {{ font:inherit; font-weight:600; font-size:14px; background:#3a3833; color:#f4f1e8; border:0; border-radius:6px; padding:8px 12px; cursor:pointer; }}
button small {{ display:block; font-weight:400; font-size:11.5px; opacity:.7; }}
button.blind {{ background:var(--red); }} button.on {{ outline:3px solid var(--red); }}
.ans {{ font-weight:700; }}
table {{ border-collapse:collapse; width:100%; font-size:14px; margin-top:8px; }} td, th {{ border-bottom:1px solid var(--line); padding:5px 8px; text-align:left; }}
.scroll {{ overflow-x:auto; }}
</style></head><body><div class="w">
<h1>zpc60: <b>ADPCM</b> hörtest</h1>
<p>Jeder Klang so, wie der FM-1 ihn spielen würde (das Sample gehalten, auf 44,1 kHz). Vergleiche <b>12 bit 40 kHz</b> (wie eine MPC60, aber nur
~4 s Platz) mit <b>ADPCM 40 kHz</b> (ein Drittel des Platzes: ~11 s). „? Blind“ spielt zufällig eins von beiden; ein zweiter Klick verrät, welches.
Gute Kopfhörer helfen; die leisen Ausklänge (Klavier, Flöte, Becken) sind die schwierigen Stellen.</p>
<table><tr><th>Speicherung</th><th>Platz</th><th>freie Bereiche (96 KB)</th><th>+ sloopDX-Bankbereich (240 KB)</th></tr>{times}</table>
{body}
<h2 style="margin-top:28px">Messwerte (SNR in dB gegen die 16-Bit-Quelle bei gleicher Rate; mehr ist besser)</h2>
<div class="scroll"><table><tr><th></th><th>12 bit 40k</th><th>μ-law 8 bit</th><th>ADPCM 5 bit</th><th>ADPCM 4 bit</th><th>ADPCM 4 bit +</th><th>12 bit 20k</th><th>ADPCM 4 bit 20k</th></tr>{detail}</table></div>
<p class="dim">ADPCM 64 / 256: Blöcke zu 64 bzw. 256 Samples (kleiner: Start und rückwärts billiger, 6 % mehr Platz). „+“: der bessere Encoder (sucht zwei Samples
voraus), gleicher Decoder. Gespielt wird „ADPCM 4 bit“ als „64 +“. ADPCM 5 bit: eigene Variante (16 Stufen statt 8), 64er-Blöcke, der bessere Encoder. μ-law: 8 Bit mit
logarithmischer Kennlinie (leise Stellen fein, laute grob), wie viele Sampler der 80er. Decoder auf diesem PC: {bench}</p>
</div><script>
let a = null, blind = {{}};
const play = (f, b) => {{ if (a) a.pause(); document.querySelectorAll("button.on").forEach(x => x.classList.remove("on")); a = new Audio(f); a.play(); if (b) b.classList.add("on"); }};
document.querySelectorAll("button[data-f]").forEach(b => b.onclick = () => play(b.dataset.f, b));
document.querySelectorAll("button.blind").forEach(b => b.onclick = () => {{
  const k = b.dataset.k, ans = b.nextElementSibling;
  if (blind[k] && !ans.textContent) {{ ans.textContent = blind[k] === "40k-12" ? "das war 12 bit" : "das war ADPCM"; return; }}
  blind[k] = Math.random() < 0.5 ? "40k-12" : "40k-a64L"; ans.textContent = ""; play(`${{k}}-${{blind[k]}}.wav`, b);
}});
</script></body></html>"""
    (OUT / "index.html").write_text(html, encoding="utf-8")


if __name__ == "__main__":
    main()
