// Frame-based async style via function values (zig 0.11 surface).
const std = @import("std");

pub const Handler = *const fn (ctx: *anyopaque) void;

pub fn dispatch(h: Handler, ctx: *anyopaque) void {
    h(ctx);
}

pub fn spawnCount(n: usize) usize {
    var total: usize = 0;
    var i: usize = 0;
    while (i < n) : (i += 1) {
        total += i;
    }
    return total;
}
