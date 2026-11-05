"""Local-only OTA protocol checks: authenticated stream and ambiguous completion."""
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest

spec=importlib.util.spec_from_file_location('gkc_ota',Path(__file__).resolve().parents[1]/'tools/heltec/ota_upload.py')
ota=importlib.util.module_from_spec(spec);spec.loader.exec_module(ota)

class OtaTests(unittest.TestCase):
    def exchange(self, result):
        errors=[]
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as udp, tempfile.TemporaryDirectory() as directory:
            udp.bind(('127.0.0.1',0));udp.settimeout(5)
            image=Path(directory)/'firmware.bin';content=b'\xe9'+bytes(range(256))*128;image.write_bytes(content)
            password='ephemeral-test-password'
            def device():
                try:
                    request,addr=udp.recvfrom(256);cmd,port,size,digest=request.decode().split()
                    self.assertEqual(cmd,'0');self.assertEqual(int(size),len(content))
                    self.assertEqual(digest,hashlib.md5(content).hexdigest())
                    nonce='testnonce';udp.sendto(f'AUTH {nonce}'.encode(),addr)
                    request,addr=udp.recvfrom(256);cmd,cnonce,response=request.decode().split()
                    self.assertEqual(cmd,'200')
                    expected=hashlib.md5(f'{hashlib.md5(password.encode()).hexdigest()}:{nonce}:{cnonce}'.encode()).hexdigest()
                    self.assertEqual(response,expected);udp.sendto(b'OK',addr)
                    with socket.create_connection(('127.0.0.1',int(port)),timeout=5) as conn:
                        received=b''
                        while len(received)<len(content):
                            chunk=conn.recv(min(733,len(content)-len(received)))
                            if not chunk:break
                            received+=chunk;conn.sendall(str(len(chunk)).encode())
                        self.assertEqual(received,content)
                        for part in result:
                            conn.sendall(part);time.sleep(.03)
                except Exception as e:errors.append(e)
            worker=threading.Thread(target=device);worker.start()
            try:
                with redirect_stdout(io.StringIO()):ota.upload('127.0.0.1',image,password,host_ip='127.0.0.1',port=udp.getsockname()[1])
            finally:
                worker.join(7)
                self.assertFalse(worker.is_alive())
                if errors:raise errors[0]
    def test_split_success_token(self):self.exchange([b'O',b'K'])
    def test_closed_connection_is_unknown_not_success(self):
        with self.assertRaisesRegex(RuntimeError,'No confirmed completion'):self.exchange([])
