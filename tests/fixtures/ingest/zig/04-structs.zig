// Structs with methods and defaults.
const Point = struct {
    x: i32 = 0,
    y: i32 = 0,

    pub fn move(self: *Point, dx: i32, dy: i32) void {
        self.x += dx;
        self.y += dy;
    }

    pub fn origin(self: Point) bool {
        return self.x == 0 and self.y == 0;
    }
};

pub fn makePoint() Point {
    return Point{ .x = 1, .y = 2 };
}
