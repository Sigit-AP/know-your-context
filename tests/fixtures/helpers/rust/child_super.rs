// super-relative use from a nested module.
use super::sibling;

pub fn boot() {
    let _ = sibling;
}
