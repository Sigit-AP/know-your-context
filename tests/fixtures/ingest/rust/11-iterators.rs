// Iterator chains and closures.
pub fn doubled(values: &[i32]) -> Vec<i32> {
    values.iter().map(|v| v * 2).collect()
}

pub fn sum_even(values: &[i32]) -> i32 {
    values.iter().filter(|v| *v % 2 == 0).sum()
}

pub fn zip_names(names: &[&str], ids: &[u32]) -> Vec<(u32, &str)> {
    ids.iter().copied().zip(names.iter().copied()).collect()
}
