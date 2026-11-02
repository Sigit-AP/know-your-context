// Error sets, error unions and try/catch.
const std = @import("std");

const ConfigError = error{
    Missing,
    Invalid,
};

fn readConfig(key: []const u8) ConfigError![]const u8 {
    if (key.len == 0) return ConfigError.Missing;
    return key;
}

pub fn mustConfig(key: []const u8) []const u8 {
    return readConfig(key) catch |err| switch (err) {
        ConfigError.Missing => "default",
        ConfigError.Invalid => unreachable,
    };
}

pub fn tryConfig(key: []const u8) ![]const u8 {
    const value = try readConfig(key);
    std.debug.print("{s}\n", .{value});
    return value;
}
