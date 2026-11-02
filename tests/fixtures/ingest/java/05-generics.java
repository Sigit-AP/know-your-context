// Generic classes, methods and bounded type parameters.
package demo;

class Box<T> {
    private final T value;

    Box(T value) {
        this.value = value;
    }

    T unwrap() {
        return value;
    }
}

class Util {
    static <T extends Comparable<T>> T maxOf(T a, T b) {
        return a.compareTo(b) >= 0 ? a : b;
    }

    static <T> List<T> singleton(T item) {
        return null;
    }
}
