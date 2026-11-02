// Closures capturing locals.
public delegate int Step(int amount);

public Step make_counter(int start) {
    int count = start;
    return (amount) => {
        count += amount;
        return count;
    };
}

public int apply_twice_vala(Step step, int value) {
    return step(step(value));
}
