#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Review Workbench — local read-only review server + headless CLI.

The browser UI keeps its decisions in localStorage. This server never reads that
storage; to aggregate decisions from another program, export them as JSON or use
the CLI (`template` / `apply` / `export`) described in README.md and
skill/SKILL.md.

Only the Python standard library is required. Rendering page images from a PDF
is optional and needs PyMuPDF (`pip install pymupdf`).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "index.html"
DEFAULT_OUT = ROOT / "out"
DEFAULT_PAGES = ROOT / "sample-pages"

TEXT_VERDICTS = {
    "agree": "通过", "通过": "通过", "pass": "通过",
    "change": "要改", "要改": "要改", "fix": "要改",
    "question": "有疑问", "有疑问": "有疑问", "doubt": "有疑问",
}
REVIEW_VERDICTS = {
    "agree": "采纳", "采纳": "采纳", "accept": "采纳",
    "reject": "不采纳", "不采纳": "不采纳", "deny": "不采纳",
    "question": "存疑", "存疑": "存疑",
}
TYPE_LABELS = {"text": "正文改动", "chart": "图表审阅", "decision": "待决问题", "review": "多方审校"}
TYPE_ORDER = ["text", "chart", "decision", "review"]


# --------------------------------------------------------------------------- data
def _read_block(html: str, sid: str):
    m = re.search(
        r'<script id="%s" type="application/json">(.*?)</script>' % re.escape(sid),
        html, re.S)
    if not m:
        raise SystemExit("index.html 中找不到数据块：%s" % sid)
    return json.loads(m.group(1))


def load_data(index_path: Path = INDEX) -> dict:
    html = index_path.read_text(encoding="utf-8-sig")
    return {
        "text": _read_block(html, "dataset"),
        "figdata": _read_block(html, "figdata"),
        "review": _read_block(html, "batch125"),
        "tone": _read_block(html, "tonedata"),
        "decisions": _read_block(html, "decdata"),
    }


def build_items(data: dict) -> list[dict]:
    items: list[dict] = []

    for d in data["text"]:
        items.append({
            "id": d["id"], "type": "text", "cat": d.get("catLabel") or "、".join(d.get("cats", [])),
            "level": d.get("level"), "loc": d.get("loc"), "before": d.get("before"),
            "after": d.get("after"), "basis": d.get("basis"), "status": d.get("status"),
            "source": d.get("source"), "gap": bool(d.get("gap")),
        })

    for f in data["figdata"].get("figs", []):
        is_appendix = bool(f.get("appendix"))
        fid = str(f["id"]) if is_appendix else "G-" + str(f["id"])
        items.append({
            "id": fid, "type": "chart", "kind": "图", "page": f.get("page"),
            "label": ("附图 " if is_appendix else "图 ") + str(f["id"]),
            "caption": f.get("cap"), "anomaly": bool(f.get("anom")),
            "anomalies": f.get("anoms") or [],
        })
    for t in data["figdata"].get("tables", []):
        num = re.sub(r"^(附)?表\s*", "", str(t["id"]))
        items.append({
            "id": "T-" + num, "type": "chart", "kind": "表", "page": t.get("page"),
            "label": str(t["id"]), "caption": t.get("cap"),
            "anomaly": bool(t.get("anom")), "anomalies": t.get("anoms") or [],
        })
    for t in data["tone"]:
        items.append({
            "id": t["id"], "type": "chart", "kind": "图", "page": t.get("page"),
            "label": "附图 " + str(t["id"]), "caption": t.get("cap"),
            "anomaly": False, "anomalies": [],
        })

    for d in data["decisions"]:
        items.append({
            "id": d["id"], "type": "decision", "loc": d.get("loc"), "title": d.get("title"),
            "question": d.get("question"), "multi": bool(d.get("multi")),
            "options": d.get("options") or [], "advice": d.get("advice"),
            "reason": d.get("reason"), "excerpt": d.get("excerpt"),
        })

    rd = data["review"]
    for r in rd.get("items", []):
        items.append({
            "id": r["id"], "type": "review", "loc": r.get("loc"), "page": r.get("page"),
            "level": r.get("level"), "models": r.get("models") or [],
            "problem": r.get("problem") or "", "quote": r.get("quote") or "",
            "rewrite": r.get("rewrite") or "", "reason": r.get("reason") or "",
            "need": bool(r.get("need")),
        })
    return items


def item_map(items: list[dict]) -> dict[tuple[str, str], dict]:
    return {(i["type"], i["id"]): i for i in items}


# --------------------------------------------------------------------------- decisions
def empty_template(items: list[dict]) -> dict:
    out = {t: {} for t in TYPE_ORDER}
    for it in items:
        if it["type"] in ("text", "chart"):
            out[it["type"]][it["id"]] = {"decision": "", "note": ""}
        elif it["type"] == "review":
            out[it["type"]][it["id"]] = {"decision": "", "note": ""}
        else:
            out[it["type"]][it["id"]] = {"choice": [], "note": ""}
    return {
        "schema": "review-workbench/decisions@1",
        "reviewer": "",
        "exportedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "allowed": {
            "text": list(dict.fromkeys(TEXT_VERDICTS.values())),
            "chart": list(dict.fromkeys(TEXT_VERDICTS.values())),
            "review": list(dict.fromkeys(REVIEW_VERDICTS.values())),
            "decision": "选项 key，可多选的条目用数组",
        },
        "items": out,
    }


def _pick(entry: dict, *keys):
    for k in keys:
        if k in entry and entry[k] not in (None, ""):
            return entry[k]
    return None


def normalize(entry: dict, it: dict) -> tuple[dict | None, list[str]]:
    errs: list[str] = []
    note = str(entry.get("note") or "").strip()
    if it["type"] in ("text", "chart", "review"):
        raw = _pick(entry, "decision", "verdict", "status")
        if raw is None:
            return None, ["缺少 decision/verdict 字段"]
        table = REVIEW_VERDICTS if it["type"] == "review" else TEXT_VERDICTS
        key = str(raw).strip()
        val = table.get(key) or table.get(key.lower())
        if not val:
            return None, ["判定值 %r 不合法，可选：%s" % (raw, "、".join(dict.fromkeys(table.values())))]
        return {"decision": val, "note": note}, errs

    raw = _pick(entry, "choice", "choices", "decision", "option")
    if raw is None:
        return None, ["缺少 choice 字段"]
    choices = raw if isinstance(raw, list) else [raw]
    choices = [str(c).strip() for c in choices if str(c).strip()]
    valid = [str(o.get("k")) for o in it.get("options") or []]
    unknown = [c for c in choices if c not in valid]
    if unknown:
        errs.append("选项 %s 不存在，可选：%s" % ("、".join(unknown), "、".join(valid)))
    choices = [c for c in choices if c in valid]
    if not choices:
        errs.append("没有有效选项")
    if not it.get("multi") and len(choices) > 1:
        errs.append("该条目为单选，收到 %d 个选项" % len(choices))
    if errs:
        return None, errs
    return {"choice": choices, "note": note}, errs


def apply_decisions(payload: dict, items: list[dict]) -> dict:
    imap = item_map(items)
    errors: list[dict] = []
    applied: dict[str, dict] = {t: {} for t in TYPE_ORDER}
    seen: set[tuple[str, str]] = set()

    grouped = {t: payload.get(t) or {} for t in TYPE_ORDER}
    for t in TYPE_ORDER:
        if not isinstance(grouped[t], dict):
            errors.append({"type": t, "id": "", "error": "该分组必须是对象（id -> 判定）"})
            grouped[t] = {}

    for t in TYPE_ORDER:
        for iid, entry in grouped[t].items():
            key = (t, str(iid))
            if key in seen:
                errors.append({"type": t, "id": iid, "error": "重复条目"})
                continue
            seen.add(key)
            it = imap.get(key)
            if not it:
                errors.append({"type": t, "id": iid, "error": "条目不存在"})
                continue
            if not isinstance(entry, dict):
                errors.append({"type": t, "id": iid, "error": "判定必须是对象"})
                continue
            norm, errs = normalize(entry, it)
            if errs:
                errors.append({"type": t, "id": iid, "error": "；".join(errs)})
                continue
            applied[t][iid] = norm

    flat = payload.get("items")
    if isinstance(flat, list):
        for entry in flat:
            if not isinstance(entry, dict):
                errors.append({"type": "", "id": "", "error": "items[] 必须是对象"})
                continue
            t = str(entry.get("type") or "").strip()
            iid = str(entry.get("id") or "").strip()
            if t not in TYPE_ORDER:
                errors.append({"type": t, "id": iid, "error": "type 必须是 text/chart/decision/review"})
                continue
            key = (t, iid)
            if key in seen:
                errors.append({"type": t, "id": iid, "error": "重复条目"})
                continue
            it = imap.get(key)
            if not it:
                errors.append({"type": t, "id": iid, "error": "条目不存在"})
                continue
            seen.add(key)
            norm, errs = normalize(entry, it)
            if errs:
                errors.append({"type": t, "id": iid, "error": "；".join(errs)})
                continue
            applied[t][iid] = norm

    counts = {t: len(applied[t]) for t in TYPE_ORDER}
    actionable = {
        t: {iid: v for iid, v in applied[t].items()
            if v.get("decision") in ("要改", "有疑问", "采纳", "存疑")}
        for t in TYPE_ORDER
    }
    unresolved = [i["id"] for i in items if (i["type"], i["id"]) not in seen]
    return {
        "schema": "review-workbench/result@1",
        "appliedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "reviewer": payload.get("reviewer") or "",
        "summary": {
            "itemsTotal": len(items),
            "applied": sum(counts.values()),
            "byType": counts,
            "errors": len(errors),
            "unresolved": len(unresolved),
        },
        "applied": applied,
        "actionable": actionable,
        "errors": errors,
        "unresolved": unresolved,
    }


def result_to_markdown(result: dict, items: list[dict]) -> str:
    imap = item_map(items)
    s = result["summary"]
    lines = [
        "# 审阅待改清单",
        "",
        "- 生成时间：%s" % result.get("appliedAt", ""),
        "- 来源：%s" % (result.get("reviewer") or "（未标注）"),
        "- 条目：共 %d 条，已判定 %d 条，其中需改动/需决定 %d 条，未判定 %d 条" % (
            s["itemsTotal"], s["applied"],
            sum(len(v) for v in result["actionable"].values()),
            s["unresolved"]),
        "",
    ]
    if result["errors"]:
        lines += ["## 校验未通过（%d 条）" % len(result["errors"]), ""]
        for e in result["errors"]:
            lines.append("- `%s/%s`：%s" % (e.get("type") or "-", e.get("id") or "-", e.get("error")))
        lines.append("")

    for t in TYPE_ORDER:
        group = result["actionable"][t]
        if not group:
            continue
        lines += ["## %s（%d 条）" % (TYPE_LABELS[t], len(group)), ""]
        for iid, v in group.items():
            it = imap[(t, iid)]
            if t == "text":
                lines += ["### %s · %s · %s" % (iid, it.get("loc") or "", v["decision"]), ""]
                lines += ["- 原文：%s" % it.get("before", ""), "- 现文：%s" % it.get("after", "")]
            elif t == "chart":
                lines += ["### %s · %s · %s" % (iid, it.get("label") or "", v["decision"]), ""]
                lines += ["- 页码：%s" % it.get("page"), "- 图注：%s" % (it.get("caption") or "")]
                if it.get("anomalies"):
                    lines.append("- 异常：%s" % "；".join(it["anomalies"]))
            elif t == "decision":
                opts = {str(o.get("k")): o.get("text") for o in it.get("options") or []}
                picked = "；".join("%s. %s" % (c, opts.get(c, "")) for c in v.get("choice", []))
                lines += ["### %s · %s · 选择 %s" % (iid, it.get("title") or "", "、".join(v.get("choice", []))), ""]
                lines += ["- 位置：%s" % (it.get("loc") or ""), "- 题目：%s" % (it.get("question") or ""),
                          "- 选择：%s" % picked]
            else:
                lines += ["### %s · %s · %s" % (iid, it.get("loc") or "", v["decision"]), ""]
                lines += ["- 问题：%s" % (it.get("problem") or it.get("quote") or ""),
                          "- 建议：%s" % (it.get("rewrite") or "")]
            lines += ["- 备注：%s" % (v.get("note") or "（无）"), ""]
    return "\n".join(lines).rstrip() + "\n"


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def write_outputs(result: dict, markdown: str, out_dir: Path) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = _stamp()
    jp = out_dir / ("review-result-%s.json" % stamp)
    mp = out_dir / ("待改清单-%s.md" % stamp)
    jp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    mp.write_text(markdown, encoding="utf-8")
    return [str(jp), str(mp)]


# --------------------------------------------------------------------------- CLI
def cmd_list(args, items):
    subset = [i for i in items if args.type in (None, "all") or i["type"] == args.type]
    if args.query:
        q = args.query.lower()
        subset = [i for i in subset if q in json.dumps(i, ensure_ascii=False).lower()]
    print(json.dumps(subset, ensure_ascii=False, indent=2))
    return 0


def cmd_show(args, items):
    imap = item_map(items)
    it = imap.get((args.type, args.id)) if args.type else next(
        (i for i in items if i["id"] == args.id), None)
    if not it:
        print(json.dumps({"ok": False, "error": "not found"}, ensure_ascii=False))
        return 2
    print(json.dumps(it, ensure_ascii=False, indent=2))
    return 0


def cmd_template(args, items):
    payload = empty_template(items)
    if args.in_file:
        src = json.loads(Path(args.in_file).read_text(encoding="utf-8-sig"))
        # carry over any judgments already present
        for t in TYPE_ORDER:
            payload["items"][t].update((src.get(t) or {}))
        for k in ("reviewer", "exportedAt"):
            if src.get(k):
                payload[k] = src[k]
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(json.dumps({"ok": True, "file": str(out), "items": len(items)}, ensure_ascii=False))
    return 0


def _load_decisions(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise SystemExit("决定文件不存在：%s" % p)
    data = json.loads(p.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise SystemExit("决定文件必须是 JSON 对象")
    if "items" in data and isinstance(data["items"], dict):
        merged = {t: data["items"].get(t, {}) for t in TYPE_ORDER}
        merged.update({k: v for k, v in data.items() if k in TYPE_ORDER})
        merged["reviewer"] = data.get("reviewer", "")
        return merged
    return data


def cmd_apply(args, items):
    try:
        payload = _load_decisions(args.in_file)
    except json.JSONDecodeError as e:
        print(json.dumps({"ok": False, "error": "JSON 解析失败：%s" % e}, ensure_ascii=False))
        return 2
    result = apply_decisions(payload, items)
    md = result_to_markdown(result, items)
    files = write_outputs(result, md, Path(args.out_dir))
    result["files"] = files
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not result["errors"] else 2


def cmd_export(args, items):
    try:
        payload = _load_decisions(args.in_file)
    except json.JSONDecodeError as e:
        print(json.dumps({"ok": False, "error": "JSON 解析失败：%s" % e}, ensure_ascii=False))
        return 2
    result = apply_decisions(payload, items)
    md = result_to_markdown(result, items)
    if args.stdout:
        sys.stdout.write(md)
        return 0 if not result["errors"] else 2
    files = write_outputs(result, md, Path(args.out_dir))
    print(json.dumps({"ok": not result["errors"], "files": files,
                      "summary": result["summary"]}, ensure_ascii=False))
    return 0 if not result["errors"] else 2


def cmd_check(args, items):
    data = load_data(INDEX)
    print(json.dumps({
        "ok": True,
        "index": str(INDEX),
        "items": len(items),
        "byType": {t: sum(1 for i in items if i["type"] == t) for t in TYPE_ORDER},
        "models": [m.get("name") for m in data["review"].get("models", [])],
    }, ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------- server
class WorkbenchServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, *, items, out_dir, pages_dir=None, pdf=None, media_dir=None):
        super().__init__(addr, handler)
        self.items = items
        self.out_dir = out_dir
        self.pages_dir = pages_dir
        self.pdf = pdf
        self.media_dir = media_dir


class Handler(BaseHTTPRequestHandler):
    server_version = "ReviewWorkbench/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (datetime.now().strftime("%H:%M:%S"), fmt % args))

    # -- helpers ---------------------------------------------------------
    def _json(self, obj, status=HTTPStatus.OK):
        body = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path, ctype: str):
        if not path.is_file():
            self._json({"ok": False, "error": "not found", "path": str(path)}, HTTPStatus.NOT_FOUND)
            return
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8-sig"))
        except json.JSONDecodeError as e:
            raise ValueError("JSON 解析失败：%s" % e)

    def _page_png(self, n: int, qs: dict):
        if self.server.pages_dir:
            for pat in ("p%03d.png" % n, "%d.png" % n, "page-%d.png" % n):
                p = self.server.pages_dir / pat
                if p.is_file():
                    self._file(p, "image/png")
                    return
        if self.server.pdf:
            try:
                import fitz  # PyMuPDF, optional
            except ImportError:
                self._json({"ok": False, "error": "未安装 PyMuPDF，无法从 PDF 渲染页面；请安装 pip install pymupdf 或使用 --pages"}, HTTPStatus.SERVICE_UNAVAILABLE)
                return
            doc = fitz.open(self.server.pdf)
            if not 1 <= n <= doc.page_count:
                self._json({"ok": False, "error": "page out of range"}, HTTPStatus.NOT_FOUND)
                return
            page = doc[n - 1]
            dpi = int(qs.get("dpi", ["150"])[0] or 150)
            pix = page.get_pixmap(dpi=dpi)
            data = pix.tobytes("png")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)
            return
        self._json({"ok": False, "error": "没有可用的页面源；用 --pages 目录或 --pdf 启动"}, HTTPStatus.NOT_FOUND)

    # -- routes ----------------------------------------------------------
    def do_GET(self):
        u = urlparse(self.path)
        path = unquote(u.path)
        qs = parse_qs(u.query)
        if path in ("/", "/index.html"):
            self._file(INDEX, "text/html; charset=utf-8")
        elif path == "/health":
            self._json({"ok": True, "service": "review-workbench", "items": len(self.server.items)})
        elif path == "/api/items":
            t = (qs.get("type") or [None])[0]
            q = (qs.get("q") or [""])[0].lower()
            subset = [i for i in self.server.items if not t or t == "all" or i["type"] == t]
            if q:
                subset = [i for i in subset if q in json.dumps(i, ensure_ascii=False).lower()]
            self._json({"ok": True, "count": len(subset), "items": subset})
        elif path.startswith("/api/items/"):
            iid = path[len("/api/items/"):]
            it = next((i for i in self.server.items if i["id"] == iid), None)
            self._json({"ok": bool(it), "item": it}, HTTPStatus.OK if it else HTTPStatus.NOT_FOUND)
        elif path.startswith("/page/"):
            raw = path[len("/page/"):]
            try:
                n = int(re.sub(r"\D", "", raw) or 0)
            except ValueError:
                n = 0
            if n <= 0:
                self._json({"ok": False, "error": "bad page"}, HTTPStatus.BAD_REQUEST)
            else:
                self._page_png(n, qs)
        elif path.startswith("/media/"):
            if not self.server.media_dir:
                self._json({"ok": False, "error": "未配置 --media"}, HTTPStatus.NOT_FOUND)
                return
            rel = path[len("/media/"):]
            p = (self.server.media_dir / rel).resolve()
            try:
                p.relative_to(self.server.media_dir.resolve())
            except ValueError:
                self._json({"ok": False, "error": "forbidden"}, HTTPStatus.FORBIDDEN)
                return
            ctype = "image/jpeg" if p.suffix.lower() in (".jpg", ".jpeg") else "image/png"
            self._file(p, ctype)
        else:
            self._json({"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self._body()
        except ValueError as e:
            self._json({"ok": False, "error": str(e)}, HTTPStatus.BAD_REQUEST)
            return

        if path == "/api/export":
            js = body.get("json")
            md = body.get("markdown")
            if js is None or md is None:
                self._json({"ok": False, "error": "需要 json 与 markdown 两个字段"}, HTTPStatus.BAD_REQUEST)
                return
            stamp = _stamp()
            out = Path(self.server.out_dir)
            out.mkdir(parents=True, exist_ok=True)
            jp = out / ("review-export-%s.json" % stamp)
            mp = out / ("待改清单-%s.md" % stamp)
            jp.write_text(json.dumps(js, ensure_ascii=False, indent=2), encoding="utf-8")
            mp.write_text(str(md), encoding="utf-8")
            self._json({"ok": True, "files": [str(jp), str(mp)]})
        elif path == "/api/apply":
            result = apply_decisions(body, self.server.items)
            files = []
            if body.get("write", True):
                result["files"] = write_outputs(
                    result, result_to_markdown(result, self.server.items), Path(self.server.out_dir))
                files = result["files"]
            self._json({
                "ok": not result["errors"],
                "summary": result["summary"],
                "applied": result["applied"],
                "actionable": result["actionable"],
                "errors": result["errors"],
                "unresolved": result["unresolved"],
                "files": files,
            })
        elif path == "/api/template":
            self._json(empty_template(self.server.items))
        else:
            self._json({"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)


def cmd_serve(args, items):
    pages = Path(args.pages) if args.pages else (DEFAULT_PAGES if DEFAULT_PAGES.is_dir() else None)
    pdf = Path(args.pdf) if args.pdf else None
    media = Path(args.media) if args.media else None
    if pdf and not pdf.is_file():
        raise SystemExit("PDF 不存在：%s" % pdf)
    if pages and not pages.is_dir():
        raise SystemExit("页面目录不存在：%s" % pages)
    server = WorkbenchServer(
        (args.host, args.port), Handler, items=items, out_dir=Path(args.out_dir),
        pages_dir=pages, pdf=pdf, media_dir=media)
    url = "http://%s:%d/" % (args.host if args.host != "0.0.0.0" else "127.0.0.1", args.port)
    print("Review Workbench 已启动：%s" % url)
    print("条目 %d 条；输出目录 %s" % (len(items), Path(args.out_dir).resolve()))
    print("页面源：%s" % (pdf or pages or "无（图表页将显示占位提示）"))
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="server.py",
        description="Review Workbench：本地只读审阅服务 + 无界面批量处理 CLI")
    sub = p.add_subparsers(dest="cmd")

    def common(sp):
        sp.add_argument("--index", default=str(INDEX), help="index.html 路径")
        sp.add_argument("--out-dir", default=str(DEFAULT_OUT), help="输出目录（默认 ./out）")

    sp = sub.add_parser("serve", help="启动本地工作台（默认端口 8770）")
    common(sp)
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8770)
    sp.add_argument("--pages", help="页面 PNG 目录（p001.png / 1.png / page-1.png）")
    sp.add_argument("--pdf", help="从 PDF 渲染页面（需要 PyMuPDF）")
    sp.add_argument("--media", help="内嵌原图目录，映射到 /media/")
    sp.add_argument("--open", action="store_true", help="启动后自动打开浏览器")

    sp = sub.add_parser("list", help="列出条目（JSON）")
    common(sp)
    sp.add_argument("--type", choices=["text", "chart", "decision", "review", "all"], default="all")
    sp.add_argument("--query", help="在条目 JSON 中做子串过滤")

    sp = sub.add_parser("show", help="查看单条（JSON）")
    common(sp)
    sp.add_argument("id")
    sp.add_argument("--type", choices=["text", "chart", "decision", "review"])

    sp = sub.add_parser("template", help="生成决定文件模板")
    common(sp)
    sp.add_argument("--in", dest="in_file", help="可选：在已有决定文件上补全")
    sp.add_argument("--out", default=str(DEFAULT_OUT / "decisions-template.json"))

    sp = sub.add_parser("apply", help="批量应用决定文件，输出结构化结果与待改清单")
    common(sp)
    sp.add_argument("--in", dest="in_file", required=True)

    sp = sub.add_parser("export", help="把决定文件导出为 Markdown 待改清单")
    common(sp)
    sp.add_argument("--in", dest="in_file", required=True)
    sp.add_argument("--stdout", action="store_true", help="直接打印 Markdown，不写文件")

    sp = sub.add_parser("check", help="自检：数据块可解析、条目计数")
    common(sp)

    args = p.parse_args(argv)
    if not args.cmd:
        args = p.parse_args(["serve"] + (argv or []))

    index_path = Path(getattr(args, "index", INDEX))
    if index_path.resolve() != INDEX:
        globals()["INDEX"] = index_path
    data = load_data(index_path)
    items = build_items(data)

    if args.cmd == "serve":
        return cmd_serve(args, items)
    if args.cmd == "list":
        return cmd_list(args, items)
    if args.cmd == "show":
        return cmd_show(args, items)
    if args.cmd == "template":
        return cmd_template(args, items)
    if args.cmd == "apply":
        return cmd_apply(args, items)
    if args.cmd == "export":
        return cmd_export(args, items)
    if args.cmd == "check":
        return cmd_check(args, items)
    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
