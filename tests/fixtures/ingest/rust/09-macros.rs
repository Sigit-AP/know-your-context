// Macro invocations and a macro_rules definition.
macro_rules! squared {
    ($x:expr) => {
        $x * $x
    };
}

pub fn report(value: i32) {
    println!("value={}", value);
    assert!(value > 0);
    let _ = vec![value; 2];
    let _ = squared!(value);
}
