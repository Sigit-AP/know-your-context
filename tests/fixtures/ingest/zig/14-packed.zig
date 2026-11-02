// packed and extern layouts, bitcasts.
const Flags = packed struct {
    ready: u1 = 0,
    err: u1 = 0,
    mode: u4 = 0,
    pad: u10 = 0,
};

const Header = extern struct {
    magic: u32,
    len: u32,
};

pub fn flagsValue(f: Flags) u16 {
    return @bitCast(f);
}

pub fn headerMagic(h: Header) u32 {
    return h.magic;
}
