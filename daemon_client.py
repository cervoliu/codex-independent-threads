"""JSON-RPC over the managed Codex daemon's Unix WebSocket."""
import base64
import hashlib
import json
import os
from pathlib import Path
import queue
import socket
import struct
import threading
import time
from thread_client import Client

class DaemonClient(Client):
    def __init__(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(10)
        self.sock.connect(str(Path.home()/'.codex/app-server-control/app-server-control.sock'))
        key = base64.b64encode(os.urandom(16)).decode()
        request = ('GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n'
                   'Connection: Upgrade\r\nSec-WebSocket-Key: '+key+
                   '\r\nSec-WebSocket-Version: 13\r\n\r\n')
        self.sock.sendall(request.encode())
        header = bytearray()
        while not header.endswith(b'\r\n\r\n'):
            if len(header) > 16384:
                raise RuntimeError('Oversized WebSocket handshake')
            header.extend(self._exact(1))
        lines = header.decode().split('\r\n')
        expected = base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        headers = dict(line.split(':',1) for line in lines[1:] if ':' in line)
        if ' 101 ' not in lines[0] or next((v.strip() for k,v in headers.items() if k.lower()=='sec-websocket-accept'),None) != expected:
            raise RuntimeError('Invalid daemon WebSocket handshake')
        self.sock.settimeout(None)
        self.inbox = queue.Queue(); self.events = []; self.request_id = 0
        self.write_lock = threading.Lock()
        threading.Thread(target=self._read, daemon=True).start()
        self.call('initialize', {'clientInfo': {'name':'independent_thread_client','title':'Independent thread client','version':'0.2.0'}})
        self.send({'method':'initialized','params':{}})

    def _exact(self,n):
        chunks = bytearray()
        while len(chunks)<n:
            part = self.sock.recv(n-len(chunks))
            if not part:
                raise EOFError('daemon closed connection')
            chunks.extend(part)
        return bytes(chunks)

    def _frame(self,opcode,payload):
        n=len(payload); mask=os.urandom(4)
        header=bytes([0x80|opcode, 0x80|n]) if n<126 else bytes([0x80|opcode,0x80|126])+struct.pack('!H',n) if n<65536 else bytes([0x80|opcode,0x80|127])+struct.pack('!Q',n)
        with self.write_lock:
            self.sock.sendall(header+mask+bytes(b^mask[i%4] for i,b in enumerate(payload)))

    def send(self,value):
        self._frame(1,json.dumps(value).encode())

    def _read(self):
        fragments=bytearray()
        try:
            while True:
                a,b=self._exact(2); opcode=a&15; n=b&127
                if n==126: n=struct.unpack('!H',self._exact(2))[0]
                elif n==127: n=struct.unpack('!Q',self._exact(8))[0]
                if n>64*1024*1024: raise RuntimeError('Oversized daemon frame')
                mask=self._exact(4) if b&128 else None
                payload=self._exact(n)
                if mask: payload=bytes(v^mask[i%4] for i,v in enumerate(payload))
                if opcode==8: break
                if opcode==9: self._frame(10,payload); continue
                if opcode==10: continue
                if opcode not in (0,1): raise RuntimeError('Unexpected WebSocket opcode')
                fragments.extend(payload)
                if a&128:
                    self.inbox.put(json.loads(fragments)); fragments.clear()
        except (OSError,EOFError,ValueError,RuntimeError):
            pass
        finally:
            self.inbox.put(None)

    def close(self):
        try: self.sock.shutdown(socket.SHUT_RDWR)
        except OSError: pass
        self.sock.close()
