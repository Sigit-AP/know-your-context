// Comptime generics via type-returning functions.
const std = @import("std");

fn Stack(comptime T: type) type {
    return struct {
        items: std.ArrayList(T),

        pub fn init(allocator: std.mem.Allocator) @This() {
            return .{ .items = std.ArrayList(T).init(allocator) };
        }

        pub fn push(self: *@This(), item: T) !void {
            try self.items.append(item);
        }
    };
}

pub fn useStack() void {
    _ = Stack;
}
