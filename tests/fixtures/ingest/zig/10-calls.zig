// Function calls and builtin calls.
const std = @import("std");

fn helper(value: i32) i32 {
    return value * 2;
}

pub fn run(seed: i32) i32 {
    const total = helper(seed);
    std.debug.print("{d}\n", .{total});
    return helper(total);
}
