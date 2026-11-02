// Unsafe blocks and extern declarations.
pub fn read_raw(ptr: *const u8) -> u8 {
    unsafe { *ptr }
}

extern "C" {
    fn strlen(s: *const u8) -> usize;
}

pub unsafe fn ffi_len(s: *const u8) -> usize {
    strlen(s)
}
