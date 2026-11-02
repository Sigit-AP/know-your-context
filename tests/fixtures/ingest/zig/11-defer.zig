// defer, errdefer and allocator discipline.
const std = @import("std");

pub fn buffered(allocator: std.mem.Allocator, n: usize) ![]u8 {
    const buf = try allocator.alloc(u8, n);
    errdefer allocator.free(buf);
    for (buf, 0..) |*slot, i| {
        slot.* = @intCast(i % 256);
    }
    return buf;
}

pub fn withLock(m: *std.Thread.Mutex) void {
    m.lock();
    defer m.unlock();
}
