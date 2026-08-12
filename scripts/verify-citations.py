#!/usr/bin/env python3
"""인용 검증 — 서브에이전트가 수집한 문헌 목록을 Crossref/arXiv API로 대조한다.

날조된 DOI와 오매칭(재출판본이 원판을 가리는 경우 등)을 기계로 잡는다.
실전에서 수집 목록의 10~20%가 오매칭이었다. 반드시 돌릴 것.

사용법:
  # 1) 검증할 목록을 JSON으로 준비 (아래 스키마)
  # 2) CROSSREF_MAILTO=you@example.com python3 verify-citations.py refs.json
  # 3) ✗ 표시된 항목은 직접 재검색해서 고친다

CROSSREF_MAILTO 는 선택이지만 설정을 권장한다 — Crossref polite pool에 들어가야
대량 조회가 제한되지 않는다. 미설정 시 경고만 내고 진행한다.

판정은 3분류다: ✓ 통과 / ✗ 불일치·부재(진짜 실패) / ✗ UNCHECKED(네트워크 실패).
UNCHECKED 를 통과로 세지 않는다 — 네트워크가 죽은 것과 인용이 옳은 것은 다르다.

refs.json 스키마:
{
  "crossref": [
    {"axis": 1, "query": "제목 키워드로 된 검색어", "expect_author": "성(Family name)"},
    {"axis": 1, "doi": "10.1145/175222.175230", "expect_author": "Grudin"}
  ],
  "arxiv": [
    {"axis": 6, "id": "2310.06770", "label": "SWE-bench"}
  ]
}

출력: 각 항목의 검증 결과 + 실패 목록 요약(exit code 1이면 실패 있음).
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

# Crossref polite pool. 환경변수로 넘기면 UA에 실린다 — 익명 요청은 우선순위가 낮다.
MAILTO = os.environ.get("CROSSREF_MAILTO", "")
UA = "repo-factory-citation-check/1.0" + (f" (mailto:{MAILTO})" if MAILTO else "")

# arXiv API는 id_list가 길면 URL 길이 제한에 걸린다. 배치를 쪼갠다.
ARXIV_BATCH = 50


def die(msg, *hints):
    """실행 가능한 에러로 중단한다. 스택트레이스를 사용자에게 던지지 않는다."""
    print(f"ERROR: {msg}", file=sys.stderr)
    for h in hints:
        print(f"  → {h}", file=sys.stderr)
    sys.exit(2)


def load_spec(path):
    """읽기·파싱·스키마 검증을 한 곳에서. 실패는 전부 실행 가능한 메시지로 바꾼다."""
    if not os.path.exists(path):
        die(f"파일이 없다: {path}", "경로를 확인하라", "스키마는 이 스크립트 상단 docstring 참조")
    if os.path.isdir(path):
        die(f"{path} 는 디렉터리다 — JSON 파일 경로를 달라", "스키마는 이 스크립트 상단 docstring 참조")
    try:
        with open(path, encoding="utf-8") as f:
            spec = json.load(f)
    except json.JSONDecodeError as e:
        die(
            f"{path} 가 올바른 JSON이 아니다 — {e.lineno}행 {e.colno}열: {e.msg}",
            "흔한 원인: 마지막 항목 뒤 쉼표, 홑따옴표, 주석",
            f"확인: python3 -m json.tool {path}",
        )
    except UnicodeDecodeError:
        die(f"{path} 를 UTF-8로 읽을 수 없다", "파일 인코딩을 UTF-8로 저장하라")
    except OSError as e:
        die(f"{path} 를 읽을 수 없다 — {e.strerror or e}", "권한(퍼미션)을 확인하라")

    if not isinstance(spec, dict):
        die(f"최상위가 JSON 객체여야 한다 (지금은 {type(spec).__name__})",
            '형식: {"crossref": [...], "arxiv": [...]}')

    if not spec.get("crossref") and not spec.get("arxiv"):
        die('"crossref" 와 "arxiv" 가 둘 다 비어 있다 — 검증할 항목이 없다',
            '형식: {"crossref": [{"query": "...", "expect_author": "..."}], "arxiv": [{"id": "2310.06770"}]}')

    problems = []
    for i, e in enumerate(spec.get("crossref") or []):
        if not isinstance(e, dict):
            problems.append(f"crossref[{i}]: 객체가 아니다 ({type(e).__name__})")
        elif not e.get("doi") and not e.get("query"):
            problems.append(f'crossref[{i}]: "doi" 또는 "query" 중 하나는 있어야 한다')
    for i, e in enumerate(spec.get("arxiv") or []):
        if not isinstance(e, dict):
            problems.append(f"arxiv[{i}]: 객체가 아니다 ({type(e).__name__})")
        elif not e.get("id"):
            problems.append(f'arxiv[{i}]: 필수 키 "id" 없음 (예: "2310.06770")')
        elif "/" in str(e["id"]) and not str(e["id"]).count("/") == 1:
            problems.append(f'arxiv[{i}]: id 형식이 이상하다 — {e["id"]!r}')

    if problems:
        print(f"ERROR: {path} 스키마 위반 {len(problems)}건", file=sys.stderr)
        for p in problems:
            print(f"  → {p}", file=sys.stderr)
        print("  스키마는 이 스크립트 상단 docstring 참조.", file=sys.stderr)
        sys.exit(2)

    return spec


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=timeout)


def crossref_by_doi(doi):
    with _get("https://api.crossref.org/works/" + urllib.parse.quote(doi)) as r:
        return json.load(r)["message"]


def crossref_search(query, rows=3, filt=None):
    url = (
        f"https://api.crossref.org/works?rows={rows}"
        f"&query.bibliographic={urllib.parse.quote(query)}"
    )
    if MAILTO:
        url += f"&mailto={urllib.parse.quote(MAILTO)}"
    if filt:
        url += f"&filter={filt}"
    with _get(url) as r:
        return json.load(r)["message"]["items"]


def meta(item):
    issued = item.get("published-print") or item.get("published-online") or item.get("issued") or {}
    year = (issued.get("date-parts") or [[None]])[0][0]
    authors = item.get("author") or []
    family = " ".join(a.get("family", "") for a in authors)
    ct = item.get("container-title") or []
    return {
        "title": (item.get("title") or ["?"])[0],
        "authors": family,
        "year": year,
        "venue": ct[0] if ct else (item.get("event", {}) or {}).get("name", ""),
        "doi": item.get("DOI", "?"),
    }


def check_crossref(entry):
    """returns (ok: bool, line: str)"""
    expect = (entry.get("expect_author") or "").lower()
    try:
        if entry.get("doi"):
            m = meta(crossref_by_doi(entry["doi"]))
            ok = (not expect) or (expect in m["authors"].lower())
        else:
            items = crossref_search(entry["query"])
            hit = None
            for it in items:
                fam = " ".join(a.get("family", "") for a in (it.get("author") or []))
                if expect and expect in fam.lower():
                    hit = it
                    break
            if hit is None and items:
                hit = items[0]
            if hit is None:
                return False, f"NOT FOUND | {entry.get('query')}"
            m = meta(hit)
            ok = (not expect) or (expect in m["authors"].lower())
        mark = "✓" if ok else "✗"
        note = "" if ok else f"  ← 기대 저자 '{entry.get('expect_author')}' 불일치 (오매칭 의심)"
        return ok, f"{mark} [{entry.get('axis','-')}] {m['title'][:70]} | {m['authors'][:40]} {m['year']} | {m['doi']}{note}"
    except urllib.error.HTTPError as e:
        # 404 = 그 DOI가 실재하지 않는다. 이건 진짜 실패(날조 탐지의 핵심 경로)다.
        what = "존재하지 않는 DOI" if e.code == 404 else f"HTTP {e.code}"
        return False, f"✗ [{entry.get('axis','-')}] {what}: {entry.get('doi') or entry.get('query')}"
    except (urllib.error.URLError, OSError) as e:
        # 네트워크 실패는 "검증 통과"가 아니다. UNCHECKED로 표시하고 실패로 센다.
        return False, f"✗ [{entry.get('axis','-')}] UNCHECKED (네트워크): {entry.get('doi') or entry.get('query')} — {e}"
    except (KeyError, ValueError, TypeError) as e:
        return False, f"✗ [{entry.get('axis','-')}] 응답 해석 실패: {entry.get('doi') or entry.get('query')} — {e}"


def _text(node, path, ns, default="?"):
    """XML 필드는 빠질 수 있다. None에 .text 를 걸면 AttributeError로 전체가 죽는다."""
    el = node.find(path, ns)
    return el.text if (el is not None and el.text) else default


def check_arxiv(entries):
    """arXiv는 배치 조회가 가능하다. 다만 id_list가 길면 URL 제한에 걸리므로 쪼갠다."""
    ns = {"a": "http://www.w3.org/2005/Atom"}
    found = {}
    net_error = None

    for start in range(0, len(entries), ARXIV_BATCH):
        chunk = entries[start:start + ARXIV_BATCH]
        ids = ",".join(str(e["id"]) for e in chunk)
        url = f"https://export.arxiv.org/api/query?id_list={ids}&max_results={len(chunk)}"
        try:
            with _get(url) as r:
                tree = ET.parse(r)
        except (urllib.error.URLError, OSError) as e:
            net_error = f"arXiv API 요청 실패: {e}"
            break
        except ET.ParseError as e:
            net_error = f"arXiv 응답이 XML이 아니다: {e}"
            break

        for el in tree.getroot().findall("a:entry", ns):
            raw = _text(el, "a:id", ns, "")
            if not raw:
                continue
            aid = raw.split("/abs/")[-1].split("v")[0]
            found[aid] = {
                "title": " ".join(_text(el, "a:title", ns).split()),
                "author": _text(el, "a:author/a:name", ns),
                "published": _text(el, "a:published", ns, "????")[:10],
            }
        if start + ARXIV_BATCH < len(entries):
            time.sleep(1.0)  # arXiv는 초당 1회 이하를 요구한다

    lines, fails = [], []
    if net_error:
        # 네트워크 실패를 "검증 통과"로 흘리면 안 된다. 전건 실패로 보고한다.
        for e in entries:
            lines.append(f"✗ [{e.get('axis','-')}] UNCHECKED arXiv:{e['id']} — {net_error}")
            fails.append(str(e["id"]))
        return fails, lines
    for e in entries:
        m = found.get(e["id"])
        if m:
            lines.append(
                f"✓ [{e.get('axis','-')}] {m['title'][:70]} | {m['author']} {m['published'][:4]} | arXiv:{e['id']}"
            )
        else:
            lines.append(f"✗ [{e.get('axis','-')}] NOT FOUND arXiv:{e['id']} ({e.get('label','')})")
            fails.append(e["id"])
    return fails, lines


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    spec = load_spec(sys.argv[1])

    if not MAILTO:
        print(
            "NOTE: CROSSREF_MAILTO 가 설정되지 않았다. Crossref polite pool 밖으로 요청되어\n"
            "      느리거나 제한될 수 있다.  예: CROSSREF_MAILTO=you@example.com python3 "
            + os.path.basename(sys.argv[0]) + " refs.json\n",
            file=sys.stderr,
        )

    failures = []

    for entry in spec.get("crossref", []):
        ok, line = check_crossref(entry)
        print(line)
        if not ok:
            failures.append(entry.get("doi") or entry.get("query"))
        time.sleep(0.6)  # polite

    arx = spec.get("arxiv", [])
    if arx:
        fails, lines = check_arxiv(arx)
        for line in lines:
            print(line)
        failures.extend(fails)

    print()
    if failures:
        print(f"검증 실패 {len(failures)}건 — 직접 재검색해서 고칠 것:")
        for f in failures:
            print("  -", f)
        return 1
    print("전 항목 검증 통과.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
