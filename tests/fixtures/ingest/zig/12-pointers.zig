// Pointer forms: single, many-item and slices.
pub fn bump(p: *i32) void {
    p.* += 1;
}

pub fn sumSlice(values: []const i32) i32 {
    var total: i32 = 0;
    for (values) |v| {
        total += v;
    }
    return total;
}

pub fn firstPtr(values: [*c]const i32) i32 {
    return values[0];
}
