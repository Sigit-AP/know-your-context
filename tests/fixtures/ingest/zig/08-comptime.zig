// comptime evaluation and inline loops.
const std = @import("std");

fn fibN(comptime n: usize) u64 {
    comptime var a: u64 = 0;
    comptime var b: u64 = 1;
    inline for (0..n) |_| {
        const t = a + b;
        a = b;
        b = t;
    }
    return a;
}

pub fn fib10() u64 {
    return fibN(10);
}
