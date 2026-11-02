// @import of std and local files.
const std = @import("std");
const util = @import("util.zig");
const math = @import("lib/math.zig");

pub fn boot() void {
    std.debug.print("ok\n", .{});
    _ = util;
    _ = math;
}
