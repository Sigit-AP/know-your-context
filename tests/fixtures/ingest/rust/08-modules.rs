// Module tree: inline mods, constants and statics.
pub mod outer {
    pub const LIMIT: u32 = 100;

    pub mod inner {
        pub fn deep() -> u32 {
            1
        }
    }

    pub fn call_deep() -> u32 {
        inner::deep()
    }
}

static COUNTER: std::sync::atomic::AtomicU32 = std::sync::atomic::AtomicU32::new(0);

pub fn next() -> u32 {
    COUNTER.fetch_add(1, std::sync::atomic::Ordering::SeqCst)
}
