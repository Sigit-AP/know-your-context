// Call expressions parking unresolved refs.
int helper(int value) {
    return value * 2;
}

int run(int seed) {
    int total = helper(seed);
    return helper(total);
}
