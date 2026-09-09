#!/usr/bin/env python3
"""Firefox/WebDriver integration tests. Requires firefox and geckodriver on PATH.

Uses a temporary localhost server and browser profile; never contacts the
public site. No Python packages or changes to generated pages are required.
"""
import argparse
import base64
import functools
import http.server
import json
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_): pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--screenshot', type=Path)
    args = parser.parse_args()
    if not shutil.which('geckodriver') or not shutil.which('firefox'):
        raise SystemExit('Install Firefox and geckodriver to run browser tests')
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(QuietHandler, directory=str(ROOT / 'site')))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f'http://127.0.0.1:{server.server_port}'
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    driver = subprocess.Popen(['geckodriver', '--host', '127.0.0.1', '--port', str(port), '--log', 'error'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f'http://127.0.0.1:{port}'
    session = None
    def call(method, path, data=None):
        req = Request(base + path, data=json.dumps(data).encode() if data is not None else None, method=method, headers={'Content-Type': 'application/json'})
        with urlopen(req, timeout=40) as response: return json.load(response)['value']
    def run(script):
        return call('POST', '/session/' + session + '/execute/sync', {'script': script, 'args': []})
    def visit(path):
        call('POST', '/session/' + session + '/url', {'url': origin + path})
    def wait_for(script):
        for _ in range(80):
            if run('return ' + script): return
            time.sleep(.1)
        raise AssertionError('Timed out: ' + script)
    def query(value):
        run('let e=document.querySelector("[data-site-search]");e.value=' + json.dumps(value) + ';e.dispatchEvent(new Event("input",{bubbles:true}));')
    try:
        for _ in range(80):
            try: call('GET', '/status'); break
            except OSError: time.sleep(.1)
        session = call('POST', '/session', {'capabilities': {'alwaysMatch': {'browserName': 'firefox', 'moz:firefoxOptions': {'args': ['-headless']}}}})['sessionId']
        call('POST', '/session/' + session + '/window/rect', {'width': 1200, 'height': 1000})
        visit('/search/?q=Смывка')
        wait_for('document.querySelector(".search-result")')
        assert run('return document.querySelector(".search-result strong").textContent') == 'Смывка'
        query('+5 к Сексозности')
        wait_for('document.querySelector(".search-result")?.getAttribute("href") === "/cards/5-to-sexterity/"')
        assert run('return new URL(location.href).searchParams.get("q")') == '+5 к Сексозности'
        run('let e=document.querySelector("[data-search-type]");e.value="FAQ";e.dispatchEvent(new Event("change"));')
        wait_for('document.querySelector("[data-search-status]").textContent === "Ничего не найдено"')
        visit('/search/')
        run('const original=window.fetch;window.fetch=(...args)=>new Promise(resolve=>setTimeout(()=>resolve(original(...args)),1000));')
        query('Смывка'); time.sleep(.25); query('эльф'); time.sleep(.25); query('')
        time.sleep(1.4)
        assert run('return document.querySelectorAll(".search-result").length') == 0
        assert 'два' in run('return document.querySelector("[data-search-status]").textContent')
        visit('/search/')
        run('const original=window.fetch;let once=true;window.fetch=(...args)=>{if(once){once=false;return Promise.reject(new Error("test"));}return original(...args);};')
        query('Смывка')
        wait_for('document.querySelector("[data-search-status]").textContent.startsWith("Не удалось")')
        query('Эпический Манчкин')
        wait_for('document.querySelector(".search-result")?.getAttribute("href") === "/faq/epic-munchkin/"')
        visit('/cards/?notes=1')
        wait_for('document.querySelectorAll(".search-result").length === 100')
        assert not run('return document.querySelector("[data-search-more]").hidden')
        run('document.querySelector("[data-search-more]").click()')
        assert run('return document.querySelectorAll(".search-result").length') == 200
        assert run('return [...document.querySelectorAll(".search-result small")].every(e=>e.textContent.includes("Есть пояснения"))')
        visit('/faq/#items-and-modifiers')
        wait_for('document.getElementById("items-and-modifiers").open')
        visit('/faq/#epic-munchkin')
        wait_for('document.getElementById("epic-munchkin").open')
        visit('/faq/epic-munchkin/#qa-portals')
        assert run('return document.querySelectorAll(".qa-item").length') == 10
        assert run('return document.querySelectorAll(".article-toc a").length') == 13
        run(r'document.querySelector(".article-toc a[href=\"#abilities-2\"]").click()')
        wait_for('location.hash === "#abilities-2"')
        visit('/')
        for width in (320, 390, 820, 1200):
            for path in ('/', '/sets/', '/cards/', '/cards/annihilation/', '/faq/', '/faq/epic-munchkin/', '/search/?q=Смывка', '/contacts/'):
                run('document.querySelectorAll("iframe").forEach(e=>e.remove());let f=document.createElement("iframe");f.style.cssText="width:' + str(width) + 'px;height:850px;border:0";f.src=' + json.dumps(path) + ';document.body.prepend(f);')
                wait_for('document.querySelector("iframe").contentDocument.querySelector("main")')
                layout = run('let w=document.querySelector("iframe").contentWindow,d=w.document,n=d.querySelector(".site-header nav");return {width:w.innerWidth,scroll:d.documentElement.scrollWidth,navWidth:n.clientWidth,navScroll:n.scrollWidth};')
                assert layout['scroll'] <= layout['width'], (width, path, layout)
                assert layout['navScroll'] <= layout['navWidth'] + 1, (width, path, layout)
        if args.screenshot:
            visit('/faq/epic-munchkin/')
            run('document.querySelector(".article-toc").scrollIntoView({behavior:"instant",block:"start"})')
            encoded = call('GET', '/session/' + session + '/screenshot')
            args.screenshot.write_bytes(base64.b64decode(encoded))
        print('Browser tests passed: search ranking, aliases, URL, filters, pagination, delayed requests, retry, FAQ anchors, 32 responsive layouts', flush=True)
    finally:
        try:
            if session: call('DELETE', '/session/' + session)
        finally:
            driver.terminate()
            driver.wait(timeout=10)
            server.shutdown()
            server.server_close()


if __name__ == '__main__': main()
