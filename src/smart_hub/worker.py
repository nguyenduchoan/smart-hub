"""A single off-loop operation; no hidden executor backlog."""
import asyncio
import threading


class SerialWorker:
    def __init__(self, name):
        self.condition = threading.Condition()
        self.request = None
        self.active_future = None
        self.busy = False
        self.stopping = False
        self.finalizer = None
        self.error = None
        self.thread = threading.Thread(target=self._run, name=name, daemon=True)

    async def __aenter__(self):
        self.loop = asyncio.get_running_loop()
        self.thread.start()
        return self

    async def call(self, function, *args):
        future = self.loop.create_future()
        with self.condition:
            if self.stopping or self.busy:
                raise RuntimeError("Worker đã dừng hoặc đang xử lý một tác vụ.")
            self.busy = True
            self.request = (future, function, args)
            self.condition.notify()
        return await future

    def _deliver(self, future, result, error):
        if not future.done():
            if error is not None:
                future.set_exception(error)
            else:
                future.set_result(result)

    def _run(self):
        try:
            while True:
                with self.condition:
                    self.condition.wait_for(lambda: self.stopping or self.request is not None)
                    if self.stopping:
                        return
                    future, function, args = self.request
                    self.request = None
                    self.active_future = future
                result, error = None, None
                try:
                    result = function(*args)
                except Exception as exc:
                    error = exc
                with self.condition:
                    self.busy = False
                    self.active_future = None
                    if not self.stopping:
                        self.loop.call_soon_threadsafe(self._deliver, future, result, error)
        finally:
            if self.finalizer is not None:
                try:
                    self.finalizer()
                except Exception as exc:
                    self.error = exc

    async def close(self, timeout=5):
        with self.condition:
            self.stopping = True
            if self.active_future is not None:
                self.active_future.cancel()
            if self.request is not None:
                self.request[0].cancel()
                self.request = None
            self.condition.notify()
        await asyncio.to_thread(self.thread.join, timeout)
        if self.thread.is_alive():
            raise RuntimeError("Worker không dừng đúng hạn; phiên FAIL, kết quả muộn bị loại.")
        if self.error is not None:
            raise self.error

    async def __aexit__(self, *_):
        await self.close()
