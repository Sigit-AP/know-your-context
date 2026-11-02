"""Classes with methods and attributes."""


class Point:
    x = 0
    y = 0

    def move(self, dx, dy):
        self.x += dx
        self.y += dy
        return self

    def origin(self):
        return self.x == 0 and self.y == 0


class Named:
    def name(self):
        return type(self).__name__
