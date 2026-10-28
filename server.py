#!/usr/bin/env python3
"""Servidor local: serve a página e roda gallery-dl -d <pasta> <link>
para baixar todas as imagens de uma pasta do Pinterest."""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HOST, PORT = "127.0.0.1", 8766  # o Conversor do Bada usa a 8765: os dois rodam juntos
ROOT = Path(__file__).parent
OUT_DIR = Path.home() / "Downloads" / "downloads-baixar-pasta-pinterest"
# Navegadores de onde o gallery-dl pega o login, para baixar pasta secreta.
BROWSERS = {"chrome", "safari", "firefox", "edge", "brave"}
# pinterest.com, br.pinterest.com, pinterest.co.uk...
PINTEREST = re.compile(r"(?:[\w-]+\.)*pinterest\.[a-z]{2,3}(?:\.[a-z]{2})?")

# gallery-dl instalado pelo Homebrew (Mac) ou pelo pip (Windows, roda como módulo do Python).
GALLERY_DL = [shutil.which("gallery-dl")] if shutil.which("gallery-dl") else [sys.executable, "-m", "gallery_dl"]

# Códigos de saída do gallery-dl (somam bits) e a dica que aparece no fim do log.
HINTS = {
    64: "Esse link não é de pasta, seção, perfil ou pin do Pinterest.",
    32: "Não achei a pasta. Se ela for secreta, marque \"Pasta secreta\" e escolha o navegador em que você está logado.",
    16: "Essa pasta precisa de login: marque \"Pasta secreta\" e escolha o navegador em que você está logado.",
    8: "O Pinterest recusou a conexão. Tente de novo daqui a pouco; se continuar, atualize o gallery-dl.",
}

# Processos do gallery-dl em andamento, para o botão Cancelar.
running = set()
running_lock = threading.Lock()


WINDOWS = sys.platform == "win32"
# Põe o processo num grupo próprio, para matar ele e os filhos juntos.
GROUP = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if WINDOWS else {"start_new_session": True}


def kill(proc):
    """Mata o gallery-dl e os filhos dele de uma vez."""
    if WINDOWS:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def open_folder(folder):
    if WINDOWS:
        os.startfile(folder)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(folder)])


def is_pinterest(url):
    """Aceita só links http(s) do Pinterest (pinterest.com, br.pinterest.com, pin.it...)."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return (parsed.scheme in ("http", "https") and not url.startswith("-")
            and (host == "pin.it" or PINTEREST.fullmatch(host) is not None))


def plural(n, singular, plural_form):
    return f"{n} {singular if n == 1 else plural_form}"


def summary(new, skipped, code, browser):
    """A última linha do log: quantos arquivos vieram ou por que deu erro."""
    if code != 0:
        hints = [text for bit, text in HINTS.items() if code & bit]
        return f"\n{hints[0]}\n" if hints else ""
    if new == skipped == 0:
        hint = "" if browser else " Se a pasta for secreta, marque \"Pasta secreta\"."
        return f"\nNenhum arquivo encontrado.{hint}\n"
    old = f", {plural(skipped, 'já estava', 'já estavam')} na pasta" if skipped else ""
    return f"\n{plural(new, 'arquivo novo', 'arquivos novos')}{old}. Salvos em {OUT_DIR}\n"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = (ROOT / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/cancel":
            with running_lock:
                procs = list(running)
            for proc in procs:
                kill(proc)
            self.send_response(204)
            self.end_headers()
            return
        if self.path == "/open":
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            open_folder(OUT_DIR)
            self.send_response(204)
            self.end_headers()
            return
        if self.path != "/download":
            return self.send_error(404)
        length = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(length) or b"{}")
        url = data.get("url", "").strip()
        if not is_pinterest(url):
            return self.fail(400, "Link inválido: use um link do Pinterest")
        browser = data.get("browser") or ""
        if browser and browser not in BROWSERS:
            return self.fail(400, "Navegador inválido")

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        cmd = [*GALLERY_DL, "-d", str(OUT_DIR)]
        if browser:
            cmd += ["--cookies-from-browser", browser]
        cmd += ["--", url]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace",
                                env={**os.environ, "PYTHONUNBUFFERED": "1"}, **GROUP)
        with running_lock:
            running.add(proc)
        # O gallery-dl escreve o caminho de cada arquivo baixado; os que já existiam vêm com "# " na frente.
        new = skipped = 0
        prefix = str(OUT_DIR)
        try:
            for line in proc.stdout:
                if line.startswith(prefix):
                    new += 1
                elif line.startswith("# " + prefix):
                    skipped += 1
                self.wfile.write(line.encode())
                self.wfile.flush()
            code = proc.wait()
            self.wfile.write(summary(new, skipped, code, browser).encode())
            self.wfile.write(f"\n__EXIT__{code}".encode())
        except (BrokenPipeError, ConnectionResetError):
            kill(proc)
        finally:
            with running_lock:
                running.discard(proc)

    def fail(self, code, message):
        """Erro em texto puro: a página mostra a mensagem direto no status."""
        body = message.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def restart_on_change():
    """Reinicia o servidor sozinho quando este arquivo muda."""
    path = Path(__file__)
    last = path.stat().st_mtime
    while True:
        time.sleep(1)
        if path.stat().st_mtime != last:
            os.environ["RELOADED"] = "1"
            os.execv(sys.executable, [sys.executable, *sys.argv])


if __name__ == "__main__":
    print(f"Rodando em http://{HOST}:{PORT}  (arquivos em {OUT_DIR})")
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
        if not os.environ.get("RELOADED"):
            webbrowser.open(f"http://{HOST}:{PORT}")
        if not WINDOWS:  # os.execv não reinicia direito no Windows
            threading.Thread(target=restart_on_change, daemon=True).start()
        server.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)
