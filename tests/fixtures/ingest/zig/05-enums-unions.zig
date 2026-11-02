// Enums, tagged unions and switch exhaustiveness.
const Colour = enum { red, green, blue };

const Payload = union(enum) {
    none,
    int: i64,
    text: []const u8,
};

pub fn weight(p: Payload) usize {
    return switch (p) {
        .none => 0,
        .int => 8,
        .text => |t| t.len,
    };
}

pub fn colourCode(c: Colour) u8 {
    return @intFromEnum(c);
}
