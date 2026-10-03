"""Versioned JSONL boundary for a future C# engine host; reference host is runnable.

No CombatSolver native adapter is claimed here. Handshake flags are assertions by
that host, not independent evidence that its semantics match the actual game.
"""
from __future__ import annotations
from collections import deque
from pathlib import Path
from queue import Queue, Empty
import subprocess
import sys
import threading
from time import monotonic
from typing import Any, Iterator
from .canonical import ContractError, UnsupportedSemantic, canonical, clone, loads, read_json
from .core import Transition, checked_value

PROTOCOL = 'spire-transition-jsonl/v1'
MAX_LINE_BYTES = 16 * 1024 * 1024

class RpcModel:
    def __init__(self, command: list[str], timeout_seconds: int = 30, cwd: str | None = None):
        if not command or any(type(x) is not str or not x for x in command):
            raise ContractError('backend command must be a nonempty string array')
        if type(timeout_seconds) is not int or timeout_seconds <= 0:
            raise ContractError('RPC timeout must be a positive integer')
        self.timeout = timeout_seconds
        self.process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        self.responses: Queue[Any] = Queue(maxsize=16)
        self.errors: deque[str] = deque(maxlen=100)
        self.request_id = 0
        self.closed = False
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        try:
            hello = self._request('hello', {})
            if hello.get('protocol') != PROTOCOL:
                raise ContractError('backend protocol mismatch')
            for flag in ('deterministic', 'fully_observed', 'lossless_state', 'exhaustive_actions'):
                if hello.get('capabilities', {}).get(flag) is not True:
                    raise ContractError('backend must explicitly declare ' + flag)
            self._identity = clone(hello['identity'])
            self._initial = self._request('initial', {})['state']
            if type(self._initial) is not dict:
                raise ContractError('RPC initial state must be an object')
        except Exception:
            self.close()
            raise

    @classmethod
    def from_config(cls, path: str | Path) -> 'RpcModel':
        config = read_json(path)
        command = [sys.executable if item == '{python}' else item for item in config['command']]
        return cls(command, config.get('timeout_seconds', 30), config.get('cwd'))

    def _read_stdout(self):
        try:
            while True:
                line = self.process.stdout.readline(MAX_LINE_BYTES + 1)
                if not line:
                    self.responses.put(EOFError('backend closed stdout'))
                    return
                if len(line) > MAX_LINE_BYTES or not line.endswith(b'\n'):
                    self.responses.put(ContractError('backend response exceeds line limit or is unterminated'))
                    return
                self.responses.put(line)
        except Exception as error:
            self.responses.put(error)

    def _read_stderr(self):
        while True:
            line = self.process.stderr.readline(8192)
            if not line:
                return
            self.errors.append(line.decode('utf-8', errors='replace').rstrip())

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self.closed:
            raise ContractError('RPC backend is closed')
        self.request_id += 1
        message = canonical({'id': self.request_id, 'method': method, 'params': params}) + b'\n'
        if len(message) > MAX_LINE_BYTES:
            raise ContractError('RPC request too large')
        try:
            # The reader threads drain output while requests are processed.
            deadline = monotonic() + self.timeout
            written: Queue[Any] = Queue(maxsize=1)
            def write_request():
                try:
                    view = memoryview(message)
                    while view:
                        count = self.process.stdin.write(view)
                        if not count: raise BrokenPipeError('zero-byte pipe write')
                        view = view[count:]
                    self.process.stdin.flush()
                    written.put(None)
                except Exception as error:
                    written.put(error)
            threading.Thread(target=write_request, daemon=True).start()
            write_result = written.get(timeout=max(0.001, deadline-monotonic()))
            if write_result is not None: raise write_result
            response = self.responses.get(timeout=max(0.001, deadline-monotonic()))
        except Empty:
            self.close()
            raise UnsupportedSemantic('backend_timeout; transition enumeration unfinished') from None
        except (BrokenPipeError, OSError) as error:
            self.close()
            raise ContractError('backend pipe failed: ' + str(error)) from error
        if isinstance(response, BaseException):
            self.close()
            raise ContractError('backend stream failed: ' + str(response))
        payload = loads(response)
        if type(payload) is not dict or payload.get('id') != self.request_id:
            raise ContractError('backend response id mismatch')
        if 'error' in payload:
            error = payload['error']
            kind = UnsupportedSemantic if error.get('kind') == 'unsupported' else ContractError
            raise kind(error.get('message', 'backend error'))
        result = payload.get('result')
        if type(result) is not dict:
            raise ContractError('backend result must be an object')
        return result

    def identity(self): return clone(self._identity)
    def initial(self): return clone(self._initial)
    def terminal_value(self, state):
        return checked_value(self._request('terminal', {'state': state})['value'])
    def transitions(self, state) -> Iterator[Transition]:
        result = self._request('expand', {'state': state})
        for item in result['transitions']:
            yield Transition(item['action'], item['state'])
        if result.get('complete') is not True:
            raise UnsupportedSemantic('; '.join(result.get('reasons', ['backend returned incomplete expansion'])))
    def close(self):
        if self.closed: return
        self.closed = True
        if self.process.poll() is None:
            self.process.kill()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            try: stream.close()
            except OSError: pass
    def __enter__(self): return self
    def __exit__(self, *args): self.close()


def serve(model, max_successors: int = 100_000) -> None:
    if type(max_successors) is not int or max_successors < 1:
        raise ContractError('max_successors must be positive')
    for line in sys.stdin.buffer:
        request_id = None
        try:
            if len(line) > MAX_LINE_BYTES:
                raise ContractError('request exceeds limit')
            request = loads(line)
            request_id = request['id']
            method, params = request['method'], request['params']
            if method == 'hello':
                result = {'protocol': PROTOCOL, 'identity': model.identity(),
                          'capabilities': dict(deterministic=True, fully_observed=True,
                                               lossless_state=True, exhaustive_actions=True)}
            elif method == 'initial':
                result = {'state': model.initial()}
            elif method == 'terminal':
                value = model.terminal_value(params['state'])
                result = {'value': list(value) if value is not None else None}
            elif method == 'expand':
                edges = []
                complete = True
                reasons = []
                try:
                    for edge in model.transitions(params['state']):
                        if len(edges) >= max_successors:
                            complete = False
                            reasons.append('reference_rpc_response_budget')
                            break
                        edges.append({'action': edge.action, 'state': edge.state})
                except UnsupportedSemantic as error:
                    complete = False
                    reasons.append(str(error))
                result = {'transitions': edges, 'complete': complete, 'reasons': reasons}
            else:
                raise ContractError('unknown RPC method: ' + method)
            response = {'id': request_id, 'result': result}
        except Exception as error:
            response = {'id': request_id, 'error': {
                'kind': 'unsupported' if isinstance(error, UnsupportedSemantic) else 'contract',
                'message': str(error)}}
        encoded = canonical(response)
        if len(encoded) > MAX_LINE_BYTES:
            encoded = canonical({'id': request_id, 'error': {
                'kind': 'unsupported', 'message': 'response_size_limit'}})
        sys.stdout.buffer.write(encoded + b'\n')
        sys.stdout.buffer.flush()
