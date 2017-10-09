<!-- markdown-toc start - Don't edit this section. Run M-x markdown-toc-generate-toc again -->

**Tornado Handle HTTPRequest Workflow**
**version: "4.5.2"**

* [创建HTTPServer](#创建HTTPServer)
   * [1 创建httpserver.HTTPServer的实例server](#1-创建httpserver.HTTPServer的实例server)
   * [2 运行server.listen](#2-运行server.listen)
   * [3 启动io_loop](#3-启动io_loop)
* [处理请求](#处理请求)
   * [1 异步读取请求内容](#1-异步读取请求内容)
   * [2 异步处理请求](#2-异步处理请求)
   * [3 异步返回响应](#3-异步返回响应)

<!-- markdown-toc end -->

# 创建HTTPServer

## 1 创建httpserver.HTTPServer的实例server
``` python
server = httpserver.HTTPServer(app, **kwargs)
```
app可以是web.Application的实例，也可以是一个接收httputil.HTTPServerRequest实例作为参数的可执行的对象；但若是第二种，返回的值必须是
自己构造完整格式的http response，即响应行，响应头，响应体。

httpserver.HTTPServer基类有util.Configurable，类方法configurable_default返回的是HTTPServer，没有通过util.Configurable的类方法
configure来指定实例化的的子类，所以返回的是HTTPServer的实例，并运行了initialize方法，初始化了许多参数。

## 2 运行server.listen
```python
def listen(self, port, address="")
    sockets = bind_sockets(port, address)  # 生成监听端口的sockets
    self.add_sockets(sockets)
```
bind_sockets是netutil.bind_sockets，返回绑定在指定端口和地址监听的的多个socket。这些socket已经完成创建，绑定，监听的阶段。
这一步也可以使用(unix)netutil.bind_unix_socket,将创建的socket绑定在一个文件上，然后开始监听。

self.add_sockets：
```python
def add_sockets(self, sockets):
    """Makes this server start accepting connections on the given sockets. # 达成的效果
    The ``sockets`` parameter is a list of socket objects such as
    those returned by `~tornado.netutil.bind_sockets`.  # sockets的来处，如上
    `add_sockets` is typically used in combination with that
    method and `tornado.process.fork_processes` to provide greater
    control over the initialization of a multi-process server.  # 多进程也会用到
    """
    if self.io_loop is None:
        self.io_loop = IOLoop.current()
    # 创建io_loop，因为需要通过io_loop将监听的socket和callback绑定起来；
    # 这里的callback是netutil.add_accept_handler里面的accept_handler函数

    for sock in sockets:
        self._sockets[sock.fileno()] = sock  # 将socket保存在_sockets里
        add_accept_handler(sock, self._handle_connection,
                           io_loop=self.io_loop)  # 执行netutil.add_accept_handler 监听sock的read事件，回调是accept_handler
```

netutil.add_accept_handler:
```python
def add_accept_handler(sock, callback, io_loop=None):
    """Adds an `.IOLoop` event handler to accept new connections on ``sock``.
    """
    if io_loop is None:
        io_loop = IOLoop.current()  # 确保有io_loop

    def accept_handler(fd, events):
        for i in xrange(_DEFAULT_BACKLOG):  # 一次最多128
            try:
                connection, address = sock.accept()  # 获取socket请求
            except socket.error as e:
                if errno_from_exception(e) in _ERRNO_WOULDBLOCK:
                    return
                if errno_from_exception(e) == errno.ECONNABORTED:
                    continue
                raise
            set_close_exec(connection.fileno())
            callback(connection, address)  # tcpserver.TCPServer._handle_connection  # 当accept到具体请求时，会执行tcpserver.TCPServer._handle_connection
    io_loop.add_handler(sock, accept_handler, IOLoop.READ)  # 当sock可read时，执行accept_handler；具体获取是在io_loop.start里
```

## 3 启动io_loop
```python
ioloop.IOLoop.instance().start()
```
启动io_loop循环，这个循环主要做三件事：处理self._callbacks；处理self._timeouts；获取epoll中发生监听事件的sockets，并执行它的回调。

# 处理请求

## 1 异步读取请求内容

上面HTTPServer已经创建完成，io_loop.start的while循环一直在运行。当来了request时，被监听的socket触发可读事件，
io_loop._impl.poll获取到所有触发事件的sockets，其中就有服务器端创建的用于监听请求的sockets。然后执行保存在io_loop._handlers里
的callback，这里是netutil.add_accept_handler里面的accept_handler函数。如上，至此tcp协议三次握手完成，客户端可以发送具体请求信息，
服务器通过读取connection(accept_handler里)获取请求内容。

socket.accept获取到socket请求和address后，执行tcpserver.TCPServer._handle_connection：
实际上_handle_connection继承自tcpserver.TCPServer，有tcp层开始处理请求的意思。

_handle_connection:
```python
def _handle_connection(self, connection, address):  # 只复制关键语句
    try:
        if self.ssl_options is not None:
            stream = SSLIOStream(connection, io_loop=self.io_loop,
                                 max_buffer_size=self.max_buffer_size,
                                 read_chunk_size=self.read_chunk_size)
        else:
            stream = IOStream(connection, io_loop=self.io_loop,
                              max_buffer_size=self.max_buffer_size,
                              read_chunk_size=self.read_chunk_size)  # 构造一个iostream，有许多读写参数和读写方法，可读写socket

        future = self.handle_stream(stream, address)  # 应用层协议解析tcp读写到的内容。一般为httpserver.HTTPServer.handle_stream
        if future is not None:  # http server时future是None,下面可见
            self.io_loop.add_future(gen.convert_yielded(future),
                                    lambda f: f.result())
```
将请求的socket(即connection)用iostream.IOStream进行封装，然后执行httpserver.HTTPServer.handle_stream。

httpserver.HTTPServer.handle_stream:
```python
def handle_stream(self, stream, address):
    context = _HTTPRequestContext(stream, address,
                                  self.protocol,
                                  self.trusted_downstream)  # 保存socket请求信息
    conn = HTTP1ServerConnection(
        stream, self.conn_params, context)  # 构造一个HTTP1ServerConnection
    self._connections.add(conn)  # 保存在_connections里
    conn.start_serving(self)  # 源头
```
即构造一个http1connection.HTTP1ServerConnection实例conn，然后运行conn.start_serving(self)

http1connection.HTTP1ServerConnection.start_serving:
```python
def start_serving(self, delegate):
    """Starts serving requests on this connection.
    :arg delegate: a `.HTTPServerConnectionDelegate`  # 即httpserver.HTTPServer的实例
    """
    assert isinstance(delegate, httputil.HTTPServerConnectionDelegate)
    self._serving_future = self._server_request_loop(delegate)
    # 实际是在执行_server_request_loop方法，这是个用gen.coroutine装饰了的异步方法，符合异步读取的设计。
    # Register the future on the IOLoop so its errors get logged.
    self.stream.io_loop.add_future(self._serving_future,
                                   lambda f: f.result())
    # io_loop.add_future(future, callback)的效果是当future set_done后，会执行io_loop.add_callback(callback, future)
    # 即future有result后，callback会以future为参数执行。这里也就是会执行self._serving_future.result()
```

http1connection.HTTP1ServerConnection._server_request_loop:
```python
@gen.coroutine
def _server_request_loop(self, delegate):  # delegate: httpserver.HTTPServer instance
    try:
        while True:
            conn = HTTP1Connection(self.stream, False,
                                   self.params, self.context)  # 构建一个is_client=False的HTTP1Connection实例
            request_delegate = delegate.start_request(self, conn)  # 见下面
            # 会运行httpserver.HTTPServer.start_request
            # request_delegate: httpserver._CallableAdapter or routing._RoutingDelegate instance. all are subclass of httputil.HTTPMessageDelegate
            try:
                ret = yield conn.read_response(request_delegate)  # 执行HTTP1Connection.read_response 见下面
            except (iostream.StreamClosedError,
                    iostream.UnsatisfiableReadError):
                return
            except _QuietException:
                # This exception was already logged.
                conn.close()
                return
            except Exception:
                gen_log.error("Uncaught exception", exc_info=True)
                conn.close()
                return
            if not ret:
                return
            yield gen.moment
    finally:
        delegate.on_close(self)
```
web.RequestHandler.finish()里面的self.request.finish()语句执行，会返回到这里，说明ret = yield conn.read_response(request_delegate)
执行完成，最后会执行delegate.on_close(self),即HTTPServer.on_close(HTTP1ServerConnection),从HTTPServer._connections里面去掉conn实例

httpserver.HTTPServer.start_request(HTTP1ServerConnection, HTTP1Connection):
```python
def start_request(self, server_conn, request_conn):
    if isinstance(self.request_callback, httputil.HTTPServerConnectionDelegate):  # request_callback是web.Application的实例时
        delegate = self.request_callback.start_request(server_conn, request_conn)  # routing._RoutingDelegate
    else:
        delegate = _CallableAdapter(self.request_callback, request_conn)  # _CallableAdapter
    if self.xheaders:
        delegate = _ProxyAdapter(delegate, request_conn)
    return delegate
```

http1connection.HTTP1Connection.read_response(request_delegate):  # request_delegate: routing._RoutingDelegate
```python
def read_response(self, delegate):
    if self.params.decompress:
        delegate = _GzipMessageDelegate(delegate, self.params.chunk_size)
    return self._read_message(delegate)  # 实际执行的是http1connection.HTTP1Connection._read_message(request_delegate)
```

http1connection.HTTP1Connection._read_message(request_delegate):
```python
@gen.coroutine
def _read_message(self, delegate):  # request_delegate: routing._RoutingDelegate
    need_delegate_close = False
    try:
        header_future = self.stream.read_until_regex(  # iostream.IOStream.read_until_regex 见下面
            b"\r?\n\r?\n",
            max_bytes=self.params.max_header_size)
        if self.params.header_timeout is None:
            header_data = yield header_future  # if和else都是为确认获得header_data，else设置了一个超时取消
        else:
            try:
                header_data = yield gen.with_timeout(
                    self.stream.io_loop.time() + self.params.header_timeout,
                    header_future,
                    io_loop=self.stream.io_loop,
                    quiet_exceptions=iostream.StreamClosedError)
            except gen.TimeoutError:
                self.close()
                raise gen.Return(False)
        start_line, headers = self._parse_headers(header_data)  # 解析发送的headers
        if self.is_client:
            start_line = httputil.parse_response_start_line(start_line)
            self._response_start_line = start_line
        else:
            start_line = httputil.parse_request_start_line(start_line)  # is_client=False
            self._request_start_line = start_line
            self._request_headers = headers

        self._disconnect_on_finish = not self._can_keep_alive(
            start_line, headers)
        need_delegate_close = True
        with _ExceptionLoggingContext(app_log):
            header_future = delegate.headers_received(start_line, headers)
            # delegate: routing._RoutingDelegate instance    httputil.HTTPMessageDelegate subclass
            # 执行routing._RoutingDelegate.headers_received(start_line, headers) 见下面
            if header_future is not None:
                yield header_future
        if self.stream is None:
            # We've been detached.
            need_delegate_close = False
            raise gen.Return(False)
        skip_body = False
        if self.is_client:
            if (self._request_start_line is not None and
                    self._request_start_line.method == 'HEAD'):
                skip_body = True
            code = start_line.code
            if code == 304:
                # 304 responses may include the content-length header
                # but do not actually have a body.
                # http://tools.ietf.org/html/rfc7230#section-3.3
                skip_body = True
            if code >= 100 and code < 200:
                # 1xx responses should never indicate the presence of
                # a body.
                if ('Content-Length' in headers or
                        'Transfer-Encoding' in headers):
                    raise httputil.HTTPInputError(
                        "Response code %d cannot have body" % code)
                # TODO: client delegates will get headers_received twice
                # in the case of a 100-continue.  Document or change?
                yield self._read_message(delegate)
        else:  # is_client=False
            if (headers.get("Expect") == "100-continue" and
                    not self._write_finished):
                self.stream.write(b"HTTP/1.1 100 (Continue)\r\n\r\n")
        if not skip_body:
            body_future = self._read_body(
                start_line.code if self.is_client else 0, headers, delegate)  # delegate: web._RoutingDelegate instance
            # 这个和上面的header_future = self.stream.read_until_regex很像，转到self._read_fix_body(content_length, delegate)
            # self._read_fixed_body会执行self.stream.read_bytes(min(self.params.chunk_size, content_length), partial=True)
            # 每读取一点body，会执行web._HandlerDelegate.data_received(body)，将数据放进web._HandlerDelegate.chunks
            if body_future is not None:
                if self._body_timeout is None:
                    yield body_future
                else:
                    try:
                        yield gen.with_timeout(
                            self.stream.io_loop.time() + self._body_timeout,
                            body_future, self.stream.io_loop,
                            quiet_exceptions=iostream.StreamClosedError)
                    except gen.TimeoutError:
                        gen_log.info("Timeout reading body from %s",
                                     self.context)
                        self.stream.close()
                        raise gen.Return(False)
        self._read_finished = True  # 读取完成
        if not self._write_finished or self.is_client:
            need_delegate_close = False
            with _ExceptionLoggingContext(app_log):
                delegate.finish()  # 执行web._HandlerDelegate.finish() 见下面
        # If we're waiting for the application to produce an asynchronous
        # response, and we're not detached, register a close callback
        # on the stream (we didn't need one while we were reading)
        if (not self._finish_future.done() and
                self.stream is not None and
                not self.stream.closed()):
            self.stream.set_close_callback(self._on_connection_close)
            yield self._finish_future
        if self.is_client and self._disconnect_on_finish:
            self.close()
        if self.stream is None:
            raise gen.Return(False)
    except httputil.HTTPInputError as e:
        gen_log.info("Malformed HTTP message from %s: %s",
                     self.context, e)
        self.close()
        raise gen.Return(False)
    finally:
        if need_delegate_close:
            with _ExceptionLoggingContext(app_log):
                delegate.on_connection_close()
        header_future = None
        self._clear_callbacks()
    raise gen.Return(True)
```

iostream.IOStream.read_until_regex(b"\r?\n\r?\n", max_bytes=self.params.max_header_size)
```python
def read_until_regex(self, regex, callback=None, max_bytes=None):
    future = self._set_read_callback(callback)  # 设置_read_future
    self._read_regex = re.compile(regex)  # 保存_read_regex
    self._read_max_bytes = max_bytes  # 保存_read_max_bytes
    try:
        self._try_inline_read()  # 执行IOStream._try_inline_read 见下面
    except UnsatisfiableReadError as e:
        # Handle this the same way as in _handle_events.
        gen_log.info("Unsatisfiable read, closing connection: %s" % e)
        self.close(exc_info=True)
        return future
    except:
        if future is not None:
            # Ensure that the future doesn't log an error because its
            # failure was never examined.
            future.add_done_callback(lambda f: f.exception())
        raise
    return future
```

iostream.IOStream._try_inline_read():
```python
def _try_inline_read(self):
    """Attempt to complete the current read operation from buffered data.
    If the read can be completed without blocking, schedules the
    read callback on the next IOLoop iteration; otherwise starts
    listening for reads on the socket.
    """
    # See if we've already got the data from a previous read
    self._run_streaming_callback()
    pos = self._find_read_pos()
    if pos is not None:
        self._read_from_buffer(pos)
        return
    self._check_closed()
    try:
        pos = self._read_to_buffer_loop()  # 会尝试从self.socket里读取(_read_from_fd)
    except Exception:
        # If there was an in _read_to_buffer, we called close() already,
        # but couldn't run the close callback because of _pending_callbacks.
        # Before we escape from this function, run the close callback if
        # applicable.
        self._maybe_run_close_callback()
        raise
    if pos is not None:
        self._read_from_buffer(pos)
        return
    # We couldn't satisfy the read inline, so either close the stream
    # or listen for new data.
    if self.closed():
        self._maybe_run_close_callback()
    else:
        self._add_io_state(ioloop.IOLoop.READ)
        # 当没有时，在self.socket上监听read事件,回调时self._handle_events,达到可读时再读取的效果
```
这里使用coroutine和yield实现了多次异步执行的效果，到此处监听为重点。每次yield出去的future和generator绑定(用Runner.run(),gen.send(future.result())()实现)
即当stream的_read_future有了结果，header_data也就有了数据。
然后继续执行http1connection.HTTP1Connection._read_message(request_delegate)的start_line, headers = self._parse_headers(header_data)

routing._RoutingDelegate.headers_received(start_line, headers):
```python
class _RoutingDelegate(httputil.HTTPMessageDelegate):
    def __init__(self, router, server_conn, request_conn):
        # router: Application server_conn: HTTP1ServerConnection request_conn: HTTP1Connection
        self.server_conn = server_conn
        self.request_conn = request_conn
        self.delegate = None
        self.router = router  # type: Router

    def headers_received(self, start_line, headers):
        request = httputil.HTTPServerRequest(
            connection=self.request_conn,
            server_connection=self.server_conn,
            start_line=start_line, headers=headers)

        self.delegate = self.router.find_handler(request)  # self.delegate: web._HandlerDelegate
        return self.delegate.headers_received(start_line, headers)  # 所以实质执行的是web._HandlerDelegate.headers_received(start_line, headers) 见下面：

    def data_received(self, chunk):
        return self.delegate.data_received(chunk)

    def finish(self):
        self.delegate.finish()

    def on_connection_close(self):
        self.delegate.on_connection_close()
```

web._HandlerDelegate.headers_received(start_line, headers):
```python
class _HandlerDelegate(httputil.HTTPMessageDelegate):
    def __init__(self, application, request, handler_class, handler_kwargs,
                 path_args, path_kwargs):
        self.application = application
        self.connection = request.connection  #  从上一层得是http1connection.HTTP1Connection
        self.request = request  # 上一层的headers_received里构建的httputil.HTTPServerRequest
        self.handler_class = handler_class  # subclass of web.RequestHandler
        self.handler_kwargs = handler_kwargs or {}
        self.path_args = path_args or []
        self.path_kwargs = path_kwargs or {}
        self.chunks = []
        self.stream_request_body = _has_stream_request_body(self.handler_class)

    def headers_received(self, start_line, headers):
        if self.stream_request_body:
            self.request.body = Future()
            return self.execute()
```
创建个web._HandlerDelegate实例，但不是stream_body的时候什么都没做。
继续执行http1connection.HTTP1Connection._read_message(request_delegate)的if header_future is not None:

web._HandlerDelegate.finish():
```python
class _HandlerDelegate(httputil.HTTPMessageDelegate):
    def __init__(self, application, request, handler_class, handler_kwargs,
                 path_args, path_kwargs):
        self.application = application
        self.connection = request.connection
        self.request = request
        self.handler_class = handler_class
        self.handler_kwargs = handler_kwargs or {}
        self.path_args = path_args or []
        self.path_kwargs = path_kwargs or {}
        self.chunks = []
        self.stream_request_body = _has_stream_request_body(self.handler_class)

    def headers_received(self, start_line, headers):
        if self.stream_request_body:
            self.request.body = Future()
            return self.execute()

    def data_received(self, data):
        if self.stream_request_body:
            return self.handler.data_received(data)
        else:
            self.chunks.append(data)

    def finish(self):
        if self.stream_request_body:
            self.request.body.set_result(None)
        else:
            self.request.body = b''.join(self.chunks)  # 保存请求的body
            self.request._parse_body()  # 解析请求的body
            self.execute()  # 执行execute

    def on_connection_close(self):
        if self.stream_request_body:
            self.handler.on_connection_close()
        else:
            self.chunks = None

    def execute(self):
        # If template cache is disabled (usually in the debug mode),
        # re-compile templates and reload static files on every
        # request so you don't need to restart to see changes
        if not self.application.settings.get("compiled_template_cache", True):
            with RequestHandler._template_loader_lock:
                for loader in RequestHandler._template_loaders.values():
                    loader.reset()
        if not self.application.settings.get('static_hash_cache', True):
            StaticFileHandler.reset()

        self.handler = self.handler_class(self.application, self.request,
                                          **self.handler_kwargs)  # 实例化web.RequestHandler的子类
        transforms = [t(self.request) for t in self.application.transforms]  # 实例化可以做压缩的对象，最后会判断要不要压缩

        if self.stream_request_body:
            self.handler._prepared_future = Future()
        # Note that if an exception escapes handler._execute it will be
        # trapped in the Future it returns (which we are ignoring here,
        # leaving it to be logged when the Future is GC'd).
        # However, that shouldn't happen because _execute has a blanket
        # except handler, and we cannot easily access the IOLoop here to
        # call add_future (because of the requirement to remain compatible
        # with WSGI)
        self.handler._execute(transforms, *self.path_args,
                              **self.path_kwargs)  # 执行web.RequestHandler的子类实例的_execute方法，至此便是处理请求的部分了
        # If we are streaming the request body, then execute() is finished
        # when the handler has prepared to receive the body.  If not,
        # it doesn't matter when execute() finishes (so we return None)
        return self.handler._prepared_future
```

## 2 异步处理请求

上一层执行self.handler._execute(transforms, *self.path_args, **self.path_kwargs),是在执行web.RequestHandler里的_execute方法。

web.RequestHandler._execute(transforms, *path_args, **kwargs):
```python
@gen.coroutine
def _execute(self, transforms, *args, **kwargs):
    """Executes this request with the given output transforms."""
    self._transforms = transforms
    try:
        if self.request.method not in self.SUPPORTED_METHODS:
            raise HTTPError(405)
        self.path_args = [self.decode_argument(arg) for arg in args]  # 保存url参数
        self.path_kwargs = dict((k, self.decode_argument(v, name=k))  # 保存url参数(命名的正则组)
                                for (k, v) in kwargs.items())
        # If XSRF cookies are turned on, reject form submissions without
        # the proper cookie
        if self.request.method not in ("GET", "HEAD", "OPTIONS") and \
                self.application.settings.get("xsrf_cookies"):
            self.check_xsrf_cookie()

        result = self.prepare()  # 执行hook func，在具体处理请求前，会执行这个方法，供开发覆盖重写
        if result is not None:
            result = yield result  # prepare可能是异步执行的
        if self._prepared_future is not None:
            # Tell the Application we've finished with prepare()
            # and are ready for the body to arrive.
            self._prepared_future.set_result(None)
        if self._finished:
            return

        if _has_stream_request_body(self.__class__):
            # In streaming mode request.body is a Future that signals
            # the body has been completely received.  The Future has no
            # result; the data has been passed to self.data_received
            # instead.
            try:
                yield self.request.body
            except iostream.StreamClosedError:
                return

        method = getattr(self, self.request.method.lower())  # 获取实例handler的具体处理处理请求的方法
        result = method(*self.path_args, **self.path_kwargs) # 执行方法
        if result is not None:
            result = yield result  # 若是异步执行，确保set_result
        if self._auto_finish and not self._finished:
            self.finish()  # 执行self.finish() 见下面
    except Exception as e:
        try:
            self._handle_request_exception(e)
        except Exception:
            app_log.error("Exception in exception handler", exc_info=True)
        if (self._prepared_future is not None and
                not self._prepared_future.done()):
            # In case we failed before setting _prepared_future, do it
            # now (to unblock the HTTP server).  Note that this is not
            # in a finally block to avoid GC issues prior to Python 3.4.
            self._prepared_future.set_result(None)
```

web.RequestHandler.finish():
```python
def finish(self, chunk=None):
    """Finishes this response, ending the HTTP request."""
    if self._finished:
        raise RuntimeError("finish() called twice")

    if chunk is not None:
        self.write(chunk)  # 将准备response的数据放进self._write_buffer，只接受dict(json)和string，列表建议用字典包起来

    # Automatically support ETags and add the Content-Length header if
    # we have not flushed any content yet.
    if not self._headers_written:
        if (self._status_code == 200 and  # 这些数据在初始化RequestHandler时执行self.clear()已预设，这里做调整
            self.request.method in ("GET", "HEAD") and
                "Etag" not in self._headers):
            self.set_etag_header()
            if self.check_etag_header():
                self._write_buffer = []
                self.set_status(304)
        if self._status_code in (204, 304):
            assert not self._write_buffer, "Cannot send body with %s" % self._status_code
            self._clear_headers_for_304()
        elif "Content-Length" not in self._headers:
            content_length = sum(len(part) for part in self._write_buffer)
            self.set_header("Content-Length", content_length)

    if hasattr(self.request, "connection"):
        # Now that the request is finished, clear the callback we
        # set on the HTTPConnection (which would otherwise prevent the
        # garbage collection of the RequestHandler when there
        # are keepalive connections)
        self.request.connection.set_close_callback(None)

    self.flush(include_footers=True)  # 执行self.flush(include_footers=True) 见下面
    self.request.finish()
    # 收尾httputil.HTTPServerRequest， http1connection.HTTP1Connection。这里会给HTTP1Connection._finish_result set_result(None)
    # 然返回HTTP1Connection._read_message继续执行完。里面会执行routing._RoutingDelegate.on_connection_close()
    self._log()  # log
    self._finished = True
    self.on_finish()  # hook func
    self._break_cycles()  # 清除self.ui
```
至此请求处理完毕，也成功返回响应进socket,然后返回到http1connection.HTTP1ServerConnection._server_request_loop,表示语句
ret = yield conn.read_response(request_delegate)已执行完，继续往下执行。

web.RequestHandler.flush():
```python
def flush(self, include_footers=False, callback=None):
    """Flushes the current output buffer to the network.
    The ``callback`` argument, if given, can be used for flow control:
    it will be run when all flushed data has been written to the socket.
    Note that only one flush callback can be outstanding at a time;
    if another flush occurs before the previous flush's callback
    has been run, the previous callback will be discarded.

    .. versionchanged:: 4.0
       Now returns a `.Future` if no callback is given.
    """
    chunk = b"".join(self._write_buffer)  # 获取读缓冲区里要发送出去的响应数据
    self._write_buffer = []
    if not self._headers_written:
        self._headers_written = True
        for transform in self._transforms:  # 调整headers
            self._status_code, self._headers, chunk = \
                transform.transform_first_chunk(
                    self._status_code, self._headers,
                    chunk, include_footers)
        # Ignore the chunk and only write the headers for HEAD requests
        if self.request.method == "HEAD":
            chunk = None

        # Finalize the cookie headers (which have been stored in a side
        # object so an outgoing cookie could be overwritten before it
        # is sent).
        if hasattr(self, "_new_cookie"):
            for cookie in self._new_cookie.values():
                self.add_header("Set-Cookie", cookie.OutputString(None))

        start_line = httputil.ResponseStartLine('',
                                                self._status_code,
                                                self._reason)
        return self.request.connection.write_headers(  # http1connection.HTTP1Connection  至此开始向socket写数据(发送响应) 见下面
            start_line, self._headers, chunk, callback=callback)
    else:
        for transform in self._transforms:
            chunk = transform.transform_chunk(chunk, include_footers)
        # Ignore the chunk and only write the headers for HEAD requests
        if self.request.method != "HEAD":
            return self.request.connection.write(chunk, callback=callback)
        else:
            future = Future()
            future.set_result(None)
            return future
```
write_headers执行完后返回上一层继续执行web.RequestHandler.finish()

## 3 异步返回相应

http1connection.HTTP1Connection.write_headers(start_line, self._headers, chunk, callback=None):
```python
def write_headers(self, start_line, headers, chunk=None, callback=None):
    """Implements `.HTTPConnection.write_headers`."""
    lines = []
    if self.is_client:
        self._request_start_line = start_line
        lines.append(utf8('%s %s HTTP/1.1' % (start_line[0], start_line[1])))
        # Client requests with a non-empty body must have either a
        # Content-Length or a Transfer-Encoding.
        self._chunking_output = (
            start_line.method in ('POST', 'PUT', 'PATCH') and
            'Content-Length' not in headers and
            'Transfer-Encoding' not in headers)
    else:  # is_client=False
        self._response_start_line = start_line
        lines.append(utf8('HTTP/1.1 %d %s' % (start_line[1], start_line[2])))
        self._chunking_output = (
            # TODO: should this use
            # self._request_start_line.version or
            # start_line.version?
            self._request_start_line.version == 'HTTP/1.1' and
            # 304 responses have no body (not even a zero-length body), and so
            # should not have either Content-Length or Transfer-Encoding.
            # headers.
            start_line.code not in (204, 304) and
            # No need to chunk the output if a Content-Length is specified.
            'Content-Length' not in headers and
            # Applications are discouraged from touching Transfer-Encoding,
            # but if they do, leave it alone.
            'Transfer-Encoding' not in headers)
        # If a 1.0 client asked for keep-alive, add the header.
        if (self._request_start_line.version == 'HTTP/1.0' and
            (self._request_headers.get('Connection', '').lower() ==
             'keep-alive')):
            headers['Connection'] = 'Keep-Alive'
    if self._chunking_output:
        headers['Transfer-Encoding'] = 'chunked'
    if (not self.is_client and
        (self._request_start_line.method == 'HEAD' or
         start_line.code == 304)):
        self._expected_content_remaining = 0
    elif 'Content-Length' in headers:
        self._expected_content_remaining = int(headers['Content-Length'])
    else:
        self._expected_content_remaining = None
    # TODO: headers are supposed to be of type str, but we still have some
    # cases that let bytes slip through. Remove these native_str calls when those
    # are fixed.
    header_lines = (native_str(n) + ": " + native_str(v) for n, v in headers.get_all())
    if PY3:
        lines.extend(l.encode('latin1') for l in header_lines)  # 预备响应数据
    else:
        lines.extend(header_lines)
    for line in lines:
        if b'\n' in line:
            raise ValueError('Newline in header: ' + repr(line))
    future = None
    if self.stream.closed():
        future = self._write_future = Future()
        future.set_exception(iostream.StreamClosedError())
        future.exception()
    else:
        if callback is not None:
            self._write_callback = stack_context.wrap(callback)
        else:
            future = self._write_future = Future()
        data = b"\r\n".join(lines) + b"\r\n\r\n"
        if chunk:
            data += self._format_chunk(chunk)  # 至此完整的http格式响应完成
        self._pending_write = self.stream.write(data)  # 通过IOStream去做往socket写入的操作 见下面
        self._pending_write.add_done_callback(self._on_write_complete)
    return future
```
当self.stream.write(data)执行成功，它返回的future(即这里的self._pending_write)会set_result(None)，当self._pending_write被
set_result,会执行self._on_write_complete。self._on_write_complete会判断有没有异常，callback，_write_future，这里是有_write_future，
会执行self._write_future.set_result(None)。即本函数HTTP1Connection.write_headers返回的future有了结果，
继续执行上一层的web.RequestHandler.flush的代码。

iostream.IOStream.write(data):
```python
def write(self, data, callback=None):
    """Asynchronously write the given data to this stream.
    If ``callback`` is given, we call it when all of the buffered write
    data has been successfully written to the stream. If there was
    previously buffered write data and an old write callback, that
    callback is simply overwritten with this new callback.
    If no ``callback`` is given, this method returns a `.Future` that
    resolves (with a result of ``None``) when the write has been
    completed.
    self._check_closed()
    if data:
        if (self.max_write_buffer_size is not None and
                self._write_buffer_size + len(data) > self.max_write_buffer_size):
            raise StreamBufferFullError("Reached maximum write buffer size")
        if self._write_buffer_frozen:
            self._pending_writes_while_frozen.append(data)
        else:
            self._write_buffer += data  # 放进iostream的write_buffer
            self._write_buffer_size += len(data)  # 更新size
        self._total_write_index += len(data)  # 监控写状态
    if callback is not None:
        self._write_callback = stack_context.wrap(callback)
        future = None
    else:
        future = TracebackFuture()
        future.add_done_callback(lambda f: f.exception())
        self._write_futures.append((self._total_write_index, future))
    if not self._connecting:
        self._handle_write()  # 尝试写入
        if self._write_buffer_size:
            self._add_io_state(self.io_loop.WRITE)
            # 若没写入成功，则监听socket写事件，可写时会执行回调_handle_events，里面会执行_handle_write
            # 保证成功写入响应，或是获取到异常。
        self._maybe_add_error_listener()
    return future
```
self._handle_write写入成功后，会从_write_future里获取到future， 并future.set_result(None)，也就是write返回的future有结果了，
回到上一层的函数继续执行，数据也成功写入进socket里了。
