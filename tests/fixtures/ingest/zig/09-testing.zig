// test blocks as real top-level structure.
const std = @import("std");

pub fn double(x: i32) i32 {
    return x * 2;
}

test "double works" {
    try std.testing.expectEqual(@as(i32, 4), double(2));
}

test "double negative" {
    try std.testing.expectEqual(@as(i32, -4), double(-2));
}
