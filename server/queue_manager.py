class QueueManager:
    def __init__(self):
        self.queue = []
        self.current = None

    def add(self, request):
        if self.current is None:
            self.current = request
            return 0
        self.queue.append(request)
        return len(self.queue)

    def finish_current(self):
        self.current = None

    def get_next(self):
        if self.queue:
            self.current = self.queue.pop(0)
            return self.current
        return None

    def get_position(self, user_id):
        for i, r in enumerate(self.queue):
            if r.get("user_id") == user_id:
                return i + 1
        return 0

    def get_size(self):
        return len(self.queue)

    def is_busy(self):
        return self.current is not None

    def to_dict(self):
        return {"current": self.current, "queue": list(self.queue)}

    def from_dict(self, data):
        self.current = data.get("current")
        self.queue = data.get("queue", [])
