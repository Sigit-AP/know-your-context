const std = @import("std");
const util = @import("util.zig");

pub fn greet(name: []const u8) void {
    helper(name);
}

fn helper(x: []const u8) void {
    std.debug.print("{s}", .{x});
}
