// Delegates as function types.
public delegate int Transformer(int value);

public int apply(Transformer transformer, int value) {
    return transformer(value);
}

public int apply_twice(Transformer transformer, int value) {
    return transformer(transformer(value));
}
