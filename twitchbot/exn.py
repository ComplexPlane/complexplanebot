class NetworkError(Exception):
    def __init__(self, msg, exn):
        self.msg = msg
        self.exn = exn


class GetError(Exception):
    def __init__(self, msg):
        self.msg = msg

