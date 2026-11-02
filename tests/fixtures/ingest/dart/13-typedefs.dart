// typedefs for function and record types.
typedef Transformer = int Function(int value);
typedef Pair = (int, int);
typedef Named = ({String first, String last});

int applyTransformer(Transformer transformer, int value) => transformer(value);
