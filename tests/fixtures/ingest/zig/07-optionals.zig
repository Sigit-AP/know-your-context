// Optionals, orelse and if-unwrapping.
pub fn firstChar(text: []const u8) ?u8 {
    if (text.len == 0) return null;
    return text[0];
}

pub fn firstOr(text: []const u8, default: u8) u8 {
    const c = firstChar(text) orelse return default;
    return c;
}

pub fn describe(text: []const u8) u8 {
    if (firstChar(text)) |c| {
        return c;
    } else {
        return 0;
    }
}
