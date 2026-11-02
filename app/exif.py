"""A long-running exiftool process (-stay_open), so each command costs no Perl start-up."""
import json
import os
import select
import subprocess
import threading
import time


class ExifTool:
    def __init__(self, exe='exiftool', timeout=120):
        self.exe = exe
        self.timeout = timeout             # seconds one command may take; generous, as a sleeping disk spins up first
        self.lock = threading.Lock()
        self.proc = None
        self.n = 0
        self._err = b''
        self._err_cv = threading.Condition()

    def _start(self):
        self.proc = subprocess.Popen([self.exe, '-stay_open', 'True', '-@', '-'],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self._err = b''
        threading.Thread(target=self._drain_stderr, args=(self.proc,), daemon=True).start()

    def _drain_stderr(self, proc):
        while True:
            chunk = os.read(proc.stderr.fileno(), 65536)
            if not chunk or proc is not self.proc:     # ended, or a stuck process that was replaced
                return
            with self._err_cv:
                self._err += chunk
                self._err_cv.notify_all()

    def _kill(self):
        self.proc.kill()
        self.proc.wait()
        self.proc = None

    def run(self, args):
        """Run one command; returns (stdout bytes, stderr text). An exiftool that doesn't answer within
        the time limit is killed (the next command starts a fresh one) and TimeoutError is raised."""
        with self.lock:
            if not self.proc or self.proc.poll() is not None:
                self._start()
            self.n += 1
            marker = f'{{ready{self.n}}}'.encode()
            lines = [*args, '-echo4', f'{{ready{self.n}}}', f'-execute{self.n}']
            self.proc.stdin.write(('\n'.join(lines) + '\n').encode('utf-8'))
            self.proc.stdin.flush()
            deadline = time.monotonic() + self.timeout

            out = b''
            fd = self.proc.stdout.fileno()
            while not out.rstrip().endswith(marker):
                if not select.select([fd], [], [], max(0, deadline - time.monotonic()))[0]:
                    self._kill()
                    raise TimeoutError(f'exiftool did not finish within {self.timeout} s and was restarted')
                chunk = os.read(fd, 1 << 20)
                if not chunk:
                    raise RuntimeError('exiftool stopped unexpectedly')
                out += chunk
            out = out.rstrip()[:-len(marker)]

            with self._err_cv:
                while marker not in self._err:
                    self._err_cv.wait(5)
                    if self.proc.poll() is not None or time.monotonic() > deadline:
                        break
                err, _, rest = self._err.partition(marker)
                self._err = rest.lstrip(b'\r\n')
            return out, err.decode('utf-8', 'replace').strip()

    # ---- helpers
    def read(self, paths, tags):
        if not paths:
            return []
        out, _ = self.run(['-json', '-n', '-G0', *[f'-{t}' for t in tags], *paths])
        text = out.decode('utf-8', 'replace').strip()
        return json.loads(text) if text else []

    def write(self, path, tag_args, common):
        out, err = self.run([*common, *tag_args, path])
        msg = out.decode('utf-8', 'replace')
        ok = ('1 image files updated' in msg or '1 image files created' in msg
              or '1 image files unchanged' in msg)
        return ok, (err or msg).strip()

    def binary(self, path, tag):
        out, _ = self.run(['-b', f'-{tag}', path])
        return out

    def version(self):
        out, _ = self.run(['-ver'])
        return out.decode().strip()
