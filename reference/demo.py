# -*- coding: utf-8 -*-
"""
demo.py — 원표봇 참조 구현 (돌려볼 수 있는 최소본)

접수 서버 · 감시자 · 쓰기 게이트를 한 파일에 담았다. 실제 운영본은 파일이 나뉘어 있지만
구조는 같다. 데이터는 전부 가상이고, 회사 고유 정보는 들어 있지 않다.

    python demo.py          →  http://127.0.0.1:8710

LLM 을 붙이려면 answer_fn() 하나만 바꾸면 된다. 기본값은 모델 없이 도는 더미다.

⛔ 이 구현이 지키는 것 (전부 실제 사고에서 나왔다)
  1. **에이전트는 파일을 쓰지 않는다.** 판정만 돌려주고, 기록은 gate_apply() 하나가 한다.
  2. 게이트가 표기를 **자동 부착**하고, 내부 메모를 답변란에서 **분리**하고,
     기록 뒤 **재조회로 확인**한다. 성공 메시지는 반영의 증거가 아니다.
  3. AI 는 문의를 **완료로 닫지 않는다** → 전부 '대기'. 사람이 보고 닫는다.
  4. 감시자는 **상태 기반**이다. 매번 로그 전체를 다시 계산하므로, 멈춰 있던 동안
     쌓인 건도 재기동 즉시 잡는다. 처리 위치를 기억하지 않는다.
  5. 서버는 **중복 기동을 거부**한다(allow_reuse_address=False). 옛 프로세스가 살아남아
     옛 코드를 서빙하는 사고를 막는다.
"""

import io
import json
import os
import re
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "demo-inquiries.jsonl")   # append-only
STATE = os.path.join(HERE, "demo-state.json")
HOST, PORT = "127.0.0.1", 8710

BOT = "원표봇"
MARK_AI = "[%s · AI 1차 안내 · 담당자 부재중]" % BOT
MARK_ACK = "[%s · 접수 확인 · 담당자 부재중]" % BOT
TAIL = "담당자 복귀 후 확인하여 달라지는 부분이 있으면 정정드리겠습니다."
ESCALATE = "업무가 멈출 정도로 급하시면 백업 담당자에게 문의해 주십시오."
DEBOUNCE = 8          # 초 — 연달아 들어온 건을 묶는다
POLL = 2              # 초 — 파일 변화만 본다(비용 0)


# ─────────────────────────────────────────────── 로그 (append-only + id 병합)
_lock = threading.Lock()


def log_append(rec):
    with _lock:
        with io.open(LOG, "a", encoding="utf-8", newline="") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def log_read():
    """같은 id 의 줄들을 병합해 최종 상태를 만든다.

    ⚠️ 첫 줄은 언제나 status='접수' 다. **줄 단위로 판정하면 끝난 건까지 다시 잡는다.**
       반드시 id 별로 병합한 뒤 판정할 것.
    """
    merged = {}
    if not os.path.exists(LOG):
        return merged
    with io.open(LOG, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
            cur = merged.setdefault(o.get("id"), {})
            for k, v in o.items():
                if v not in ("", None):
                    cur[k] = v
    merged.pop(None, None)
    return merged


def new_id():
    return "INQ-" + time.strftime("%y%m%d-%H%M%S")


# ─────────────────────────────────────────────── 쓰기 게이트
def gate_compose(verdict, public):
    """표기·꼬리말·에스컬레이션을 **강제로** 붙인다. 에이전트가 빠뜨릴 수 없다."""
    body = (public or "").strip()
    for m in (MARK_AI, MARK_ACK):
        if body.startswith(m):
            body = body[len(m):].strip()
    head = MARK_ACK if verdict == "hold" else MARK_AI
    if not body:
        body = "접수되었습니다. 담당자 확인 후 답변드리겠습니다."
    if verdict in ("hold", "escalate") and "백업 담당자" not in body:
        body += "\n" + ESCALATE
    if verdict != "hold" and TAIL not in body:
        body += "\n" + TAIL
    return head + "\n" + body + "\n(기재 " + time.strftime("%Y-%m-%d %H:%M") + ")"


def gate_apply(iid, verdict, public, note=""):
    """**파일에 닿는 유일한 통로.** 에이전트에게는 이 함수를 부를 권한조차 주지 않는다.

    note(내부 메모)는 답변란에 넣지 않는다 — 사용자 화면으로 새는 경로를 아예 없앤다.
    """
    text = gate_compose(verdict, public)
    log_append({"id": iid, "status": "대기", "a": text})   # AI 는 '완료'로 닫지 않는다

    # 재조회 검증 — 기록했다는 응답이 아니라 파일을 다시 읽어 확인한다
    again = log_read().get(iid) or {}
    ok = (again.get("a") == text)
    journal({"event": "answered" if ok else "verify_fail",
             "id": iid, "verdict": verdict, "note": note})
    return ok


def gate_ack(iid):
    """접수 즉시 붙이는 안내. **LLM 을 부르지 않는다(비용 0).**
    어떤 장애 상황에서도 사용자 화면에 최소한의 응답은 남는다."""
    log_append({"id": iid, "status": "대기",
                "a": MARK_ACK + "\n접수되었습니다. 확인 후 답변드리겠습니다.\n" + ESCALATE})
    journal({"event": "ack", "id": iid})


def journal(rec):
    rec = dict(rec)
    rec["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with io.open(os.path.join(HERE, "demo-journal.jsonl"), "a",
                 encoding="utf-8", newline="") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ─────────────────────────────────────────────── 에이전트 (교체 지점)
def answer_fn(items):
    """여기를 바꿔 끼운다. 입력은 문의 목록, 출력은 판정 목록.

    ⛔ 이 함수는 **파일을 쓰지 않는다.** 오직 판정만 돌려준다.
       실제 구현에서는 여기서 LLM 을 호출하고, 응답에서 JSON 을 꺼내면 된다.
       모델에게 줄 도구는 **읽기 전용**으로 제한하는 것이 안전하다.

    verdict: answer(확정) / dev(개발요청) / escalate(직접 처리 불가) / hold(판단 보류)
    """
    out = []
    for it in items:
        q = (it.get("q") or "")
        if "비밀번호" in q or "권한" in q:
            out.append({"id": it["id"], "verdict": "escalate",
                        "public": "권한·계정 관련은 제가 직접 처리해 드릴 수 없습니다.",
                        "note": "자격증명 계열 — 규칙상 답변 금지"})
        elif len(q.strip()) < 5:
            out.append({"id": it["id"], "verdict": "hold", "public": "",
                        "note": "내용 판별 불가"})
        else:
            out.append({"id": it["id"], "verdict": "answer",
                        "public": "문의하신 내용은 안내 문서 3장에 정리돼 있습니다.\n"
                                  "확인하시고 더 궁금하신 점은 이어서 남겨주십시오.",
                        "note": "데모 응답 — 실제로는 여기서 모델이 조사한다"})
    return out


# ─────────────────────────────────────────────── 감시자 (상태 기반)
def pending(seen):
    """처리 대상 계산. **매번 전체를 다시 본다** — 어디까지 했는지 기억하지 않는다.

    사람이 직접 쓴 답이 있으면 영구 제외한다(원격 개입을 덮어쓰지 않기 위해).
    """
    out = []
    for iid, rec in sorted(log_read().items()):
        a = rec.get("a") or ""
        if a and not a.startswith("[" + BOT):
            seen.setdefault(iid, {})["human"] = True
        s = seen.setdefault(iid, {})
        if s.get("human") or (rec.get("status") or "접수") == "완료":
            continue
        if s.get("hash") == (rec.get("q") or ""):
            continue                      # 이 내용으로는 이미 답했다
        out.append(iid)
    return out


def watcher():
    seen = {}
    sig, last_change = None, 0.0
    while True:
        try:
            st = os.stat(LOG) if os.path.exists(LOG) else None
            cur = (st.st_mtime, st.st_size) if st else None
            if cur != sig:
                sig, last_change = cur, time.time()
            if last_change and time.time() - last_change >= DEBOUNCE:
                last_change = 0.0
                ids = pending(seen)
                merged = log_read()
                for iid in ids:
                    if not seen[iid].get("ack"):
                        gate_ack(iid)
                        seen[iid]["ack"] = True
                if ids:
                    items = [{"id": i, "q": merged[i].get("q")} for i in ids]
                    for a in answer_fn(items):          # ← 에이전트는 판정만
                        if gate_apply(a["id"], a.get("verdict", "hold"),
                                      a.get("public", ""), a.get("note", "")):
                            seen[a["id"]]["hash"] = merged[a["id"]].get("q") or ""
            time.sleep(POLL)
        except Exception as e:                          # 감시자는 죽지 않는다
            journal({"event": "loop_error", "err": str(e)[:200]})
            time.sleep(5)


# ─────────────────────────────────────────────── 화면
PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>원표봇 데모</title>
<style>
 body{margin:0;background:#f6f7f9;color:#1c2430;font-family:system-ui,sans-serif;font-size:14px}
 .wrap{max-width:680px;margin:0 auto;padding:28px 18px 60px}
 h1{font-size:20px;margin:0 0 4px} .sub{color:#5b6675;margin:0 0 20px;font-size:13px}
 .card{background:#fff;border:1px solid #dfe3e8;border-radius:10px;padding:18px;margin-bottom:14px}
 input,textarea{width:100%;padding:9px 11px;border:1px solid #dfe3e8;border-radius:6px;
   font:inherit;box-sizing:border-box}
 textarea{min-height:110px}
 label{display:block;font-weight:700;margin:12px 0 6px;font-size:13px}
 button{background:#1a6fb5;color:#fff;border:0;border-radius:6px;padding:10px 18px;
   font:inherit;font-weight:700;cursor:pointer;margin-top:14px}
 .st{display:inline-block;padding:2px 10px;border-radius:11px;font-size:12px;font-weight:700;
   background:#eee;color:#555}
 .a{margin-top:9px;padding:10px 12px;background:#f4f8fb;border-left:3px solid #1a6fb5;
   white-space:pre-wrap;border-radius:0 6px 6px 0}
 .q{font-weight:700;white-space:pre-wrap}
 .id{font-size:12px;color:#8a94a0;font-family:monospace}
</style></head><body><div class="wrap">
<h1>문의 접수 (데모)</h1>
<p class="sub">담당자가 자리를 비운 사이 원표봇이 1차 대응합니다. 가상 데이터입니다.</p>
<div class="card">
  <label>성함</label><input id="name" placeholder="예: 홍길동">
  <label>문의 내용</label><textarea id="q" placeholder="무엇이 막히셨는지 적어주세요."></textarea>
  <button id="send">접수하기</button>
  <div id="msg" class="sub" style="margin-top:10px"></div>
</div>
<div class="card">
  <label>내 문의 보기</label>
  <input id="who" placeholder="접수하실 때 적으신 성함">
  <button id="load">조회</button>
  <div id="list"></div>
</div>
<script>
function esc(s){return String(s==null?"":s).replace(/[&<>"]/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c];});}
function v(id){return (document.getElementById(id).value||"").trim();}
document.getElementById("send").onclick = async function(){
  if(!v("name") || !v("q")){ document.getElementById("msg").textContent="성함과 내용을 적어주세요."; return; }
  var r = await fetch("/api/new",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({name:v("name"), q:v("q")})});
  var j = await r.json();
  document.getElementById("msg").textContent = j.ok
    ? ("접수되었습니다: " + j.id + " — 잠시 후 아래에서 조회해 보세요.")
    : ("접수하지 못했습니다: " + j.error);
  document.getElementById("who").value = v("name");
};
document.getElementById("load").onclick = async function(){
  var r = await fetch("/api/mine?name=" + encodeURIComponent(v("who")));
  var j = await r.json();
  var box = document.getElementById("list");
  if(!j.items.length){ box.innerHTML = "<p class=sub>접수하신 건이 없습니다.</p>"; return; }
  box.innerHTML = j.items.map(function(d){
    return "<div style='margin-top:14px'><span class=id>" + esc(d.id) + "</span> "
      + "<span class=st>" + esc(d.status) + "</span>"
      + "<div class=q>" + esc(d.q) + "</div>"
      + (d.a ? "<div class=a>" + esc(d.a) + "</div>" : "<p class=sub>아직 답변 전입니다.</p>")
      + "</div>";
  }).join("");
};
</script></div></body></html>
"""


class SingleServer(ThreadingHTTPServer):
    """중복 기동을 거부한다. 두 번째 기동이 조용히 성공하면 옛 코드가 계속 서빙된다."""
    allow_reuse_address = False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, ctype, body):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj):
        self._send(200, "application/json; charset=utf-8",
                   json.dumps(obj, ensure_ascii=False))

    def do_GET(self):
        p = urlparse(self.path)
        if p.path in ("/", "/index.html"):
            return self._send(200, "text/html; charset=utf-8", PAGE)
        if p.path == "/api/mine":
            who = (parse_qs(p.query).get("name") or [""])[0].strip()
            rows = [r for r in log_read().values()
                    if r.get("name") and r["name"].strip() == who]
            rows.sort(key=lambda r: r.get("id", ""), reverse=True)
            # ⛔ 공개 응답은 화이트리스트로만 — 필드가 늘어도 자동으로 새지 않는다
            return self._json({"items": [{"id": r.get("id"), "status": r.get("status", "접수"),
                                          "q": r.get("q", ""), "a": r.get("a", "")}
                                         for r in rows]})
        return self._send(404, "text/plain; charset=utf-8", "없는 경로입니다.")

    def do_POST(self):
        if urlparse(self.path).path != "/api/new":
            return self._json({"ok": False, "error": "없는 경로입니다."})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            d = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return self._json({"ok": False, "error": "요청을 읽지 못했습니다."})
        name = str(d.get("name") or "").strip()[:40]
        q = str(d.get("q") or "").strip()[:2000]
        if not name or not q:
            return self._json({"ok": False, "error": "성함과 내용을 적어주세요."})
        iid = new_id()
        log_append({"id": iid, "name": name, "q": q, "status": "접수",
                    "date": time.strftime("%Y-%m-%d")})
        return self._json({"ok": True, "id": iid})


def main():
    threading.Thread(target=watcher, daemon=True).start()
    print("")
    print("  원표봇 데모  http://%s:%d/" % (HOST, PORT))
    print("  · 접수하면 %d초 뒤 감시자가 집어 1차 응답을 답니다." % DEBOUNCE)
    print("  · 로그: %s (append-only)" % os.path.basename(LOG))
    print("  · Ctrl+C 로 종료")
    print("")
    try:
        SingleServer((HOST, PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("종료했습니다.")
    except OSError as e:
        print("포트 %d 를 열 수 없습니다: %s" % (PORT, e))
        print("이미 떠 있는 데모가 없는지 확인해 주십시오.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
